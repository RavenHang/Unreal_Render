import unreal
import os
import json
from pathlib import Path


# =============================
# 可配置参数
# =============================
QUEUE_ASSET_PATH = "/Game/Cinematics/EditorMoviePipelineQueue"
OUTPUT_JSONL_DIR = r"D:\project\camera_control\Content\Python\dataset\jsonl"
DEFAULT_CAPTION = "camera motion clip"

_executor_ref = None


# =============================
# 基础工具
# =============================
def _safe_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    out = "".join("_" if c in bad else c for c in str(name))
    out = out.strip()
    return out if out else "unknown_video"


def _find_output_setting(job):
    cfg = job.get_configuration()
    return cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)


def _resolve_mp4_path(job):
    """
    根据 MRQ 输出配置推导 mp4 文件路径。
    注意：如果你的 file_name_format 包含更多 token，可在这里继续替换。
    """
    output_setting = _find_output_setting(job)
    out_dir = output_setting.output_directory.path
    file_fmt = output_setting.file_name_format or "{sequence_name}"

    seq_name = "unknown_sequence"
    if job.sequence:
        try:
            seq_name = job.sequence.get_asset_name()
        except Exception:
            seq_name = "unknown_sequence"

    job_name = seq_name
    if hasattr(job, "job_name") and job.job_name:
        job_name = job.job_name

    filename = file_fmt
    filename = filename.replace("{sequence_name}", seq_name)
    filename = filename.replace("{job_name}", job_name)

    # mp4 通常是整段视频，不需要 frame token
    filename = filename.replace("{frame_number}", "")
    filename = filename.replace("{frame_number_shot}", "")
    filename = filename.replace("{shot_name}", "")
    filename = filename.replace("..", ".").strip("._- ")

    if not filename:
        filename = _safe_filename(job_name)

    return str(Path(out_dir) / f"{filename}.mp4")


def _rotator_to_matrix4x4(transform: unreal.Transform):
    """
    输出 4x4 行主序矩阵（list[list[float]]）
    """
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


def _set_player_to_frame(player, frame_id: int):
    """
    兼容写法：通过 PlaybackParams 跳到指定帧并评估。
    """
    params = unreal.MovieSceneSequencePlaybackParams()
    params.set_editor_property("position_type", unreal.MovieScenePositionType.FRAME)
    params.set_editor_property("frame", unreal.FrameTime(unreal.FrameNumber(frame_id), 0.0))
    player.set_playback_position(params)
    player.pause()


def _frame_in_section(section, frame_id: int):
    """
    判断 frame 是否在 section 生效范围内。
    """
    try:
        has_start = section.has_start_frame()
        has_end = section.has_end_frame()
        start = int(section.get_start_frame()) if has_start else -2**31
        end = int(section.get_end_frame()) if has_end else 2**31 - 1
        # UE 通常 [start, end)
        return (frame_id >= start) and (frame_id < end)
    except Exception:
        return True


def _get_active_camera_for_frame(level_sequence, world, frame_id: int):
    """
    在当前帧找 camera cut 对应的相机对象。
    """
    movie_scene = level_sequence.get_movie_scene()
    tracks = movie_scene.get_tracks()

    target_binding_guid = None

    for track in tracks:
        if isinstance(track, unreal.MovieSceneCameraCutTrack):
            sections = track.get_sections()
            for sec in sections:
                if not _frame_in_section(sec, frame_id):
                    continue

                cut_sec = unreal.MovieSceneCameraCutSection.cast(sec)
                if cut_sec:
                    try:
                        binding_id = cut_sec.get_camera_binding_id()
                        target_binding_guid = binding_id.get_editor_property("guid")
                    except Exception:
                        target_binding_guid = None
                if target_binding_guid:
                    break

            if target_binding_guid:
                break

    if not target_binding_guid:
        return None

    try:
        objs = level_sequence.locate_bound_objects(target_binding_guid, world)
    except Exception:
        objs = []

    if not objs:
        return None

    # 可能是 CineCameraActor 或其他绑定对象，优先找 Actor
    for obj in objs:
        if isinstance(obj, unreal.CineCameraActor):
            return obj
    return objs[0]


def _write_video_jsonl(video_id: str, records: list, output_dir: str):
    os.makedirs(output_dir, exist_ok=True)
    file_path = os.path.join(output_dir, f"{_safe_filename(video_id)}.jsonl")

    with open(file_path, "w", encoding="utf-8") as f:
        for row in records:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    unreal.log(f"[Dataset] 写入: {file_path}, frames={len(records)}")


# =============================
# 采样逻辑
# =============================
def _sample_one_job_frames(job):
    level_sequence = None
    if not job.sequence:
        unreal.log_warning(f"[Dataset] job={job.job_name} 没有 sequence，跳过")
        return []

    try:
        # SoftObjectPath -> asset
        level_sequence = unreal.EditorAssetLibrary.load_asset(job.sequence.to_string())
    except Exception:
        # 某些版本 job.sequence 可能已经是对象引用
        level_sequence = job.sequence

    if not level_sequence:
        unreal.log_warning(f"[Dataset] job={job.job_name} sequence 加载失败，跳过")
        return []

    world = unreal.EditorLevelLibrary.get_editor_world()
    settings = unreal.MovieSceneSequencePlaybackSettings()
    player, _ = unreal.LevelSequencePlayer.create_level_sequence_player(world, level_sequence, settings)

    start_frame = int(level_sequence.get_playback_start())
    end_frame = int(level_sequence.get_playback_end())

    if end_frame <= start_frame:
        unreal.log_warning(f"[Dataset] job={job.job_name} 播放范围为空，跳过")
        return []

    mp4_path = _resolve_mp4_path(job)

    video_id = level_sequence.get_name()
    if hasattr(job, "job_name") and job.job_name:
        video_id = str(job.job_name)

    records = []
    for frame_id in range(start_frame, end_frame):
        _set_player_to_frame(player, frame_id)

        cam_actor = _get_active_camera_for_frame(level_sequence, world, frame_id)
        if cam_actor is None:
            continue

        cam_comp = None
        try:
            cam_comp = cam_actor.get_cine_camera_component()
        except Exception:
            cam_comp = None
        if cam_comp is None:
            continue

        transform = cam_actor.get_actor_transform()
        fov = float(cam_comp.get_editor_property("field_of_view"))

        records.append({
            "video_id": video_id,
            "frame_id": int(frame_id),
            "mp4_filepath": mp4_path,
            "camera_fov": float(fov),
            "camera_transform": _rotator_to_matrix4x4(transform),
            "caption": DEFAULT_CAPTION
        })

    return records


# =============================
# MRQ 回调与入口
# =============================
def _on_executor_finished(executor, success):
    unreal.log(f"[MRQ] 渲染完成 success={success}")

    subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    queue = subsystem.get_queue()
    jobs = queue.get_jobs()

    for job in jobs:
        try:
            records = _sample_one_job_frames(job)
            if not records:
                unreal.log_warning(f"[Dataset] job={job.job_name} 未采样到有效帧")
                continue

            video_id = records[0]["video_id"]
            _write_video_jsonl(video_id, records, )

        except Exception as e:
            unreal.log_error(f"[Dataset] job={job.job_name} 导出失败: {e}")


def render_queue_and_export_jsonl():
    """
    渲染 MRQ 队列，并在完成后按视频导出 JSONL。
    """
    global _executor_ref

    subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)

    # 如果你已经在 MRQ UI 里配好了队列，可注释掉这段 load_queue
    queue_asset = unreal.EditorAssetLibrary.load_asset(QUEUE_ASSET_PATH)
    if queue_asset:
        try:
            subsystem.load_queue(queue_asset)
        except Exception as e:
            unreal.log_warning(f"[MRQ] load_queue 失败，继续使用当前队列: {e}")

    queue = subsystem.get_queue()
    jobs = queue.get_jobs()
    if not jobs:
        unreal.log_error("[MRQ] 当前队列为空")
        return

    _executor_ref = unreal.MoviePipelinePIEExecutor()

    _executor_ref.on_executor_finished_delegate.add_callable_unique(_on_executor_finished)

    unreal.log(f"[MRQ] 开始渲染，jobs={len(jobs)}")
    subsystem.render_queue_with_executor_instance(_executor_ref)


# 运行入口：
# render_queue_and_export_jsonl()