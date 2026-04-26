import unreal
import os
import json
import re
import glob
import subprocess

# -----------------------------
# 配置
# -----------------------------
QUEUE_ASSET_PATH = os.environ.get("QUEUE_ASSET_PATH", "/Game/Cinematics/EditorMoviePipelineQueue")
MRQ_CONFIG_PATH = os.environ.get("MRQ_CONFIG_PATH", "/Game/Cinematics/MoviePipelineQueueConfig")
OUTPUT_ROOT_DIR = os.environ.get("OUTPUT_ROOT_DIR", r"D:\dataset\output")
MAP_PATH = os.environ.get("MAP_PATH", "/Game/Downtown_West/Maps/Town")
ENCODE_FPS = int(os.environ.get("ENCODE_FPS", "15"))

_executor_ref = None
_job_output_info_for_encode = {}


# -----------------------------
# 工具函数
# -----------------------------
def _safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip().strip(".")


def _extract_level_name_from_map_path(map_path: str) -> str:
    if not map_path:
        return "unknown_level"
    return _safe_filename(map_path.split("/")[-1]) or "unknown_level"


def _get_sequence_name(job) -> str:
    seq = _resolve_sequence(job)
    if seq:
        try:
            return _safe_filename(seq.get_name()) or "unnamed_sequence"
        except Exception:
            pass

    job_name = job.job_name if job.job_name else "unnamed_sequence"
    return _safe_filename(job_name) or "unnamed_sequence"


def _build_output_dir(level_name: str, sequence_name: str) -> str:
    out_dir = os.path.join(OUTPUT_ROOT_DIR, level_name, sequence_name)
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def _get_matrix_from_transform(transform: unreal.Transform):
    loc = transform.translation
    rot = transform.rotation.rotator()
    scale = transform.scale3d

    x_axis = unreal.MathLibrary.get_forward_vector(rot)
    y_axis = unreal.MathLibrary.get_right_vector(rot)
    z_axis = unreal.MathLibrary.get_up_vector(rot)

    x_axis = unreal.Vector(x_axis.x * scale.x, x_axis.y * scale.x, x_axis.z * scale.x)
    y_axis = unreal.Vector(y_axis.x * scale.y, y_axis.y * scale.y, y_axis.z * scale.y)
    z_axis = unreal.Vector(z_axis.x * scale.z, z_axis.y * scale.z, z_axis.z * scale.z)

    return [
        [float(x_axis.x), float(y_axis.x), float(z_axis.x), float(loc.x)],
        [float(x_axis.y), float(y_axis.y), float(z_axis.y), float(loc.y)],
        [float(x_axis.z), float(y_axis.z), float(z_axis.z), float(loc.z)],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _resolve_sequence(job):
    seq_soft_path = job.get_editor_property("sequence")

    if isinstance(seq_soft_path, unreal.LevelSequence):
        return seq_soft_path

    soft_ref = unreal.SystemLibrary.conv_soft_obj_path_to_soft_obj_ref(seq_soft_path)
    loaded_asset = unreal.EditorAssetLibrary.load_asset(soft_ref.get_path_name())
    if not loaded_asset:
        unreal.log_error("找到路径但无法加载资产")
        return None

    return loaded_asset


def _get_active_camera(level_sequence, player, current_frame):
    """
    根据传入的帧数获取激活的相机 Actor
    """
    camera_cut_tracks = level_sequence.find_tracks_by_type(unreal.MovieSceneCameraCutTrack)
    if not camera_cut_tracks:
        return None

    camera_cut_track = camera_cut_tracks[0]
    active_binding_id = None

    for section in camera_cut_track.get_sections():
        start_frame_data = section.get_start_frame()
        end_frame_data = section.get_end_frame()

        start_frame = getattr(start_frame_data, "value", start_frame_data)
        end_frame = getattr(end_frame_data, "value", end_frame_data)

        if start_frame is None or end_frame is None:
            continue

        if start_frame <= current_frame < end_frame:
            active_binding_id = section.get_camera_binding_id()
            break

    if not active_binding_id:
        return None

    try:
        bound_objects = player.get_bound_objects(active_binding_id)
    except Exception as e:
        unreal.log_error(f"获取绑定对象失败: {e}")
        bound_objects = None

    if bound_objects and len(bound_objects) > 0:
        return bound_objects[0]

    return None


def _sample_camera_data(job):
    """核心采样逻辑"""
    level_sequence = _resolve_sequence(job)
    if not level_sequence:
        unreal.log_error(f"无法加载Sequence: {job.job_name}")
        return []

    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    start_frame = level_sequence.get_playback_start()
    end_frame = level_sequence.get_playback_end()

    settings = unreal.MovieSceneSequencePlaybackSettings()
    player, _ = unreal.LevelSequencePlayer.create_level_sequence_player(world, level_sequence, settings)

    records = []
    for frame in range(start_frame, end_frame):
        frame_time = unreal.FrameTime(unreal.FrameNumber(frame))
        player.set_playback_position(
            unreal.MovieSceneSequencePlaybackParams(
                frame_time,
                position_type=unreal.MovieScenePositionType.FRAME
            )
        )

        cam_actor = _get_active_camera(level_sequence, player, frame)
        if not cam_actor:
            continue

        cam_comp = cam_actor.get_cine_camera_component()
        transform = cam_actor.get_actor_transform()

        records.append(
            {
                "frame": frame,
                "fov": float(cam_comp.field_of_view),
                "focal_length": float(cam_comp.current_focal_length),
                "matrix": _get_matrix_from_transform(transform),
            }
        )

    return records


def _configure_png_output(cfg, render_dir: str, file_stub: str):
    """
    把输出格式固定为 PNG，并移除常见视频编码输出设置。
    """
    removable_keywords = (
        "AppleProRes",
        "AvidDNx",
        "CommandLineEncoder",
        "VideoOutput",
    )
    for setting in list(cfg.get_all_settings()):
        class_name = setting.get_class().get_name()
        if any(k in class_name for k in removable_keywords):
            cfg.remove_setting(setting)

    # 添加 PNG 序列输出
    cfg.find_or_add_setting_by_class(unreal.MoviePipelineImageSequenceOutput_PNG)

    # 通用输出设置
    out_setting = cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
    out_setting.set_editor_property("output_directory", unreal.DirectoryPath(path=render_dir))
    out_setting.set_editor_property("file_name_format", f"{file_stub}.{{frame_number}}")


def _to_ffmpeg_pattern(first_path: str, ext: str) -> str:
    """
    将 Unreal 输出的 xxx.0001.<ext> 推断为 ffmpeg 可读的 xxx.%04d.<ext>
    """
    m = re.match(rf"^(.*)\.(\d+)\.{ext}$", first_path, flags=re.IGNORECASE)
    if not m:
        return ""
    prefix, digits = m.group(1), m.group(2)
    return f"{prefix}.%0{len(digits)}d.{ext}"


def _extract_frame_index(path: str) -> int:
    base = os.path.basename(path)
    m = re.search(r"\.(\d+)\.[^.]+$", base)
    if not m:
        return -1
    return int(m.group(1))


def _cleanup_png_sequence(render_dir: str, sequence_name: str):
    png_glob = os.path.join(render_dir, f"{sequence_name}.*.png")
    png_files = sorted(glob.glob(png_glob))
    removed = 0

    for p in png_files:
        try:
            os.remove(p)
            removed += 1
        except Exception as e:
            unreal.log_warning(f"[{sequence_name}] 删除 PNG 失败: {p}, err={e}")

    unreal.log(f"[{sequence_name}] PNG 清理完成，删除 {removed} 张")


def _request_editor_exit():
    """
    在无界面批处理渲染结束后，主动请求编辑器退出。
    """
    try:
        unreal.SystemLibrary.quit_editor()
    except Exception as e:
        unreal.log_warning(f"调用 quit_editor 失败，尝试命令退出: {e}")
        try:
            world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
            unreal.SystemLibrary.execute_console_command(world, "QUIT_EDITOR")
        except Exception as e2:
            unreal.log_error(f"自动退出失败，请手动结束进程: {e2}")


def _encode_mp4_from_png_dir(render_dir: str, sequence_name: str, fps: int = 24):
    """
    从 render_dir 中查找 <sequence_name>.*.png，并调用 ffmpeg 合成 mp4。
    从第 1 帧开始合成（跳过 0000）。
    """
    png_glob = os.path.join(render_dir, f"{sequence_name}.*.png")
    png_files = sorted(glob.glob(png_glob))
    if not png_files:
        unreal.log_warning(f"[{sequence_name}] 未找到 PNG 序列，跳过转码: {png_glob}")
        return

    start_file = None
    for p in png_files:
        idx = _extract_frame_index(p)
        if idx >= 1:
            start_file = p
            break

    if not start_file:
        unreal.log_warning(f"[{sequence_name}] 未找到从第1帧开始的 PNG，跳过转码")
        return

    start_number = _extract_frame_index(start_file)
    input_pattern = _to_ffmpeg_pattern(start_file, "png")
    if not input_pattern:
        unreal.log_warning(f"[{sequence_name}] 无法推断序列编号格式，跳过转码")
        return

    out_mp4 = os.path.join(render_dir, f"{sequence_name}.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-framerate", str(fps),
        "-start_number", str(start_number),
        "-i", input_pattern,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        "-preset", "slow",
        out_mp4,
    ]

    unreal.log(f"[{sequence_name}] 开始转码 MP4: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.stdout:
            unreal.log(result.stdout)
        if result.stderr:
            unreal.log(result.stderr)
        unreal.log(f"[{sequence_name}] MP4 生成成功: {out_mp4}")
        _cleanup_png_sequence(render_dir, sequence_name)
    except FileNotFoundError:
        unreal.log_error("未找到 ffmpeg，请先在 Linux 镜像中安装 ffmpeg 并确保在 PATH 中")
    except subprocess.CalledProcessError as e:
        unreal.log_error(f"[{sequence_name}] ffmpeg 转码失败，退出码: {e.returncode}")
        if e.stdout:
            unreal.log_error(e.stdout)
        if e.stderr:
            unreal.log_error(e.stderr)


# -----------------------------
# 业务流程
# -----------------------------
def _on_executor_finished(executor, success):
    if not success:
        unreal.log_error("渲染任务部分失败")
        _request_editor_exit()
        return

    unreal.log("所有视频渲染及数据提取任务结束，开始合成 MP4...")
    for idx in sorted(_job_output_info_for_encode.keys()):
        _, sequence_name, render_dir = _job_output_info_for_encode[idx]
        _encode_mp4_from_png_dir(render_dir, sequence_name, fps=ENCODE_FPS)

    unreal.log("所有 MP4 合成结束。")
    _request_editor_exit()


def render_queue_and_export_dataset():
    global _executor_ref
    global _job_output_info_for_encode

    unreal.EditorLoadingAndSavingUtils.load_map(MAP_PATH)

    subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    queue_asset = unreal.EditorAssetLibrary.load_asset(QUEUE_ASSET_PATH)
    if queue_asset:
        subsystem.load_queue(queue_asset)

    queue = subsystem.get_queue()
    jobs = list(queue.get_jobs())
    if not jobs:
        unreal.log_warning("队列中没有任务，结束。")
        return

    os.makedirs(OUTPUT_ROOT_DIR, exist_ok=True)
    level_name = _extract_level_name_from_map_path(MAP_PATH)

    # 每个 job 统一输出到 OUTPUT_ROOT/level_name/sequence_name
    job_output_info = {}  # idx -> (level_name, sequence_name, render_dir)
    for idx, job in enumerate(jobs):
        sequence_name = _get_sequence_name(job)
        render_dir = _build_output_dir(level_name, sequence_name)
        job_output_info[idx] = (level_name, sequence_name, render_dir)

    # 供回调阶段使用（渲染完成后自动转码）
    _job_output_info_for_encode = dict(job_output_info)

    # 渲染前提取相机数据并写 jsonl
    for idx, job in enumerate(jobs):
        level_name_i, sequence_name, render_dir = job_output_info[idx]
        unreal.log(f"正在渲染前提取相机数据: {sequence_name}")

        try:
            data = _sample_camera_data(job)
            if data:
                jsonl_obj = {
                    "level_name": level_name_i,
                    "sequence_name": sequence_name,
                    "video_id": sequence_name,
                    "video_path": f"{render_dir}/{sequence_name}.####.png",
                    "frame_count": len(data),
                    "camera_trajectory": data,
                    "text_prompt": "",
                }

                out_path = os.path.join(render_dir, f"{sequence_name}.jsonl")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(jsonl_obj, ensure_ascii=False) + "\n")

                unreal.log(f"成功写入数据: {out_path}")
        except Exception as e:
            unreal.log_error(f"提取数据失败: {str(e)}")

    # 配置渲染任务：输出改为 PNG + 唯一目录
    for idx, job in enumerate(jobs):
        _, sequence_name, render_dir = job_output_info[idx]

        cfg = job.get_configuration()
        preset = unreal.EditorAssetLibrary.load_asset(MRQ_CONFIG_PATH)
        if preset:
            cfg.copy_from(preset)

        job.map = unreal.SoftObjectPath(MAP_PATH)
        _configure_png_output(cfg, render_dir, sequence_name)
        job.set_configuration(cfg)

        unreal.log(f"[{sequence_name}] 输出目录: {render_dir}")

    _executor_ref = unreal.MoviePipelinePIEExecutor()
    _executor_ref.set_is_rendering_offscreen(True)
    _executor_ref.on_executor_finished_delegate.add_callable_unique(_on_executor_finished)

    subsystem.render_queue_with_executor_instance(_executor_ref)


if __name__ == "__main__":
    render_queue_and_export_dataset()