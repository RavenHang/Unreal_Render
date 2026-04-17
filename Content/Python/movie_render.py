import unreal
import os
import json
import re
from pathlib import Path

# -----------------------------
# 配置（支持环境变量）
# -----------------------------
QUEUE_ASSET_PATH = os.environ.get("QUEUE_ASSET_PATH", "/Game/Cinematics/EditorMoviePipelineQueue")

OUTPUT_JSON_DIR = os.environ.get(
    "OUTPUT_JSON_DIR",
    r"D:\dataset\json"
)

RENDER_OUTPUT_DIR = os.environ.get(
    "RENDER_OUTPUT_DIR",
    r"D:\dataset\movies"
)

VIDEO_EXT = os.environ.get("VIDEO_EXT", "mp4").lstrip(".")
DEFAULT_CAPTION = ""

_executor_ref = None


# -----------------------------
# 工具函数
# -----------------------------
def _safe_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad else c for c in str(name))
    out = out.strip().strip(".")
    return out if out else "unknown_job"


def _rotator_to_matrix4x4(transform: unreal.Transform):
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


def _find_output_setting(job):
    cfg = job.get_configuration()
    return cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)


def _get_job_name(job):
    return str(job.job_name) if job.job_name else "unknown_job"


def _extract_asset_path_from_sequence_ref(seq_ref) -> str:
    if not seq_ref:
        return ""

    if isinstance(seq_ref, unreal.LevelSequence):
        return seq_ref.get_path_name()

    if isinstance(seq_ref, unreal.SoftObjectPath):
        return str(seq_ref.asset_path_name)

    s = str(seq_ref).strip()
    match = re.search(r"path='([^']+)'", s)
    if match:
        return match.group(1)

    # 4. 普通字符串判断
    if s.startswith("/Game/") or s.startswith("/All/"):
        return s

    return ""


def _resolve_level_sequence_from_job(job):
    seq_ref = job.get_editor_property("sequence")

    seq_path = _extract_asset_path_from_sequence_ref(seq_ref)
    if not seq_path:
        return None, ""

    asset = unreal.EditorAssetLibrary.load_asset(seq_path)
    if not asset or not isinstance(asset, unreal.LevelSequence):
        return None, seq_path

    return asset, seq_path


def _resolve_video_path(job):
    output_setting = _find_output_setting(job)
    # 这里直接使用脚本定义的 RENDER_OUTPUT_DIR，因为我们要确保和渲染设置一致
    out_dir = RENDER_OUTPUT_DIR
    file_fmt = output_setting.get_editor_property("file_name_format") or "{sequence_name}"
    seq_name = "unknown_sequence"
    level_sequence, _ = _resolve_level_sequence_from_job(job)
    if level_sequence:
        seq_name = level_sequence.get_name()

    job_name = _get_job_name(job)
    filename = file_fmt.replace("{sequence_name}", seq_name).replace("{job_name}", job_name)
    # 清理非法字符
    filename = re.sub(r'[\\/*?:"<>|]', "", filename).strip("._- ")

    if not filename:
        filename = job_name

    return str(Path(out_dir) / f"{filename}.{VIDEO_EXT}")


def _get_active_camera_from_sequence(level_sequence, world):
    movie_scene = level_sequence.get_movie_scene()
    tracks = movie_scene.get_tracks()

    camera_binding_guid = None
    for t in tracks:
        if isinstance(t, unreal.MovieSceneCameraCutTrack):
            sections = t.get_sections()
            if sections:
                cut_sec = unreal.MovieSceneCameraCutSection.cast(sections[0])
                if cut_sec:
                    binding_id = cut_sec.get_camera_binding_id()
                    camera_binding_guid = binding_id.get_editor_property("guid")
                break

    if not camera_binding_guid:
        return None

    try:
        bound = level_sequence.locate_bound_objects(camera_binding_guid, world)
        return bound[0] if bound else None
    except:
        return None


def _sample_one_job_frames(job):
    level_sequence, seq_path = _resolve_level_sequence_from_job(job)
    if not level_sequence:
        unreal.log_warning(f"[Dataset] Job {job.job_name} 无法加载序列资产，路径: {seq_path}")
        return []

    world = unreal.EditorLevelLibrary.get_editor_world()
    start_frame = int(level_sequence.get_playback_start())
    end_frame = int(level_sequence.get_playback_end())

    settings = unreal.MovieSceneSequencePlaybackSettings()
    player, _ = unreal.LevelSequencePlayer.create_level_sequence_player(world, level_sequence, settings)

    records = []
    video_id = _get_job_name(job)
    video_path = _resolve_video_path(job)
    print('输出队列', file_fmt)

    for frame_id in range(start_frame, end_frame):
        params = unreal.MovieSceneSequencePlaybackParams(
            frame=unreal.FrameTime(unreal.FrameNumber(frame_id), 0.0),
            position_type=unreal.MovieScenePositionType.FRAME
        )
        player.set_playback_position(params)

        cam_actor = _get_active_camera_from_sequence(level_sequence, world)
        if not cam_actor: continue

        cam_comp = cam_actor.get_cine_camera_component()
        if not cam_comp: continue

        transform = cam_actor.get_actor_transform()
        fov = float(cam_comp.get_editor_property("field_of_view"))

        records.append({
            "video_id": str(video_id),
            "frame_id": int(frame_id),
            "video_filepath": video_path,
            "camera_fov": fov,
            "camera_transform": _rotator_to_matrix4x4(transform),
            "caption": DEFAULT_CAPTION
        })

    return records


def _write_job_json(job_name: str, records: list, output_dir: str):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    file_path = os.path.join(output_dir, f"{_safe_filename(job_name)}.json")
    payload = {"video_id": job_name, "records": records}

    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    unreal.log(f"[Dataset] JSON已导出: {file_path}")


def _apply_output_dir_to_jobs(queue):
    for job in queue.get_jobs():
        output_setting = _find_output_setting(job)
        # 使用 set_editor_property 确保属性被正确写入实例
        output_setting.set_editor_property("output_directory", unreal.DirectoryPath(RENDER_OUTPUT_DIR))
        # 强制设置文件名格式，避免生成带版本号或帧号的复杂子目录结构
        output_setting.set_editor_property("file_name_format", "{sequence_name}")
        unreal.log(f"[MRQ] Job '{job.job_name}' 输出路径已重置为: {RENDER_OUTPUT_DIR}")


# -----------------------------
# MRQ 回调
# -----------------------------
def _on_executor_finished(executor, success):
    unreal.log(f"[MRQ] 渲染任务结束，成功状态: {success}")
    subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    queue = subsystem.get_queue()
    for job in queue.get_jobs():
        job_name = _get_job_name(job)
        try:
            job_records = _sample_one_job_frames(job)
            if job_records:
                _write_job_json(job_name, job_records, OUTPUT_JSON_DIR)
            else:
                unreal.log_warning(f"[Dataset] Job '{job_name}' 采样记录为空，检查Sequence设置")
        except Exception as e:
            unreal.log_error(f"[Dataset] 处理 Job '{job_name}' 时出错: {e}")


def render_queue_and_export_dataset():
    global _executor_ref

    subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)

    # 加载 Queue 资产
    queue_asset = unreal.EditorAssetLibrary.load_asset(QUEUE_ASSET_PATH)
    if queue_asset:
        subsystem.load_queue(queue_asset)

    queue = subsystem.get_queue()
    if not queue.get_jobs():
        unreal.log_error("[MRQ] 队列为空，请检查 QUEUE_ASSET_PATH 是否正确")
        return

    # 1. 关键步骤：应用输出路径
    _apply_output_dir_to_jobs(queue)

    # 2. 启动渲染
    _executor_ref = unreal.MoviePipelinePIEExecutor()
    _executor_ref.on_executor_finished_delegate.add_callable_unique(_on_executor_finished)

    unreal.log(f"[MRQ] 启动渲染引擎...")
    subsystem.render_queue_with_executor_instance(_executor_ref)


# 执行
if __name__ == "__main__":
    render_queue_and_export_dataset()