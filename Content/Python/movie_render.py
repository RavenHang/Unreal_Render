import unreal
import os
import json
import re

# -----------------------------
# 配置
# -----------------------------
QUEUE_ASSET_PATH = os.environ.get("QUEUE_ASSET_PATH", "/Game/Cinematics/EditorMoviePipelineQueue")
MRQ_CONFIG_PATH = os.environ.get("MRQ_CONFIG_PATH", "/Game/Cinematics/MoviePipelineQueueConfig")
OUTPUT_JSON_DIR = os.environ.get("OUTPUT_JSON_DIR", r"D:\dataset")
RENDER_OUTPUT_DIR = os.environ.get("RENDER_OUTPUT_DIR", r"D:\dataset\movies")
MAP_PATH = os.environ.get("MAP_PATH", "/Game/Downtown_West/Maps/Demo_Environment")

_executor_ref = None

# -----------------------------
# 工具函数
# -----------------------------
def _safe_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip().strip(".")

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
    loaded_asset = unreal.EditorAssetLibrary.load_asset(unreal.SystemLibrary.conv_soft_obj_path_to_soft_obj_ref(seq_soft_path).get_path_name())
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
        
        start_frame = getattr(start_frame_data, 'value', start_frame_data)
        end_frame = getattr(end_frame_data, 'value', end_frame_data)
        
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
    print(start_frame, end_frame)
    for frame in range(start_frame, end_frame):
        time = unreal.FrameTime(unreal.FrameNumber(frame))
        player.set_playback_position(unreal.MovieSceneSequencePlaybackParams(time, position_type=unreal.MovieScenePositionType.FRAME))
        
        cam_actor = _get_active_camera(level_sequence, player, frame)
        if not cam_actor:
            print(f"frame {frame} 没有相机")
            continue

        cam_comp = cam_actor.get_cine_camera_component()
        transform = cam_actor.get_actor_transform()
        
        records.append({
            "frame": frame,
            "fov": float(cam_comp.field_of_view),
            "focal_length": float(cam_comp.current_focal_length),
            "matrix": _get_matrix_from_transform(transform)
        })
    
    return records

# -----------------------------
# 业务流程
# -----------------------------
def _on_executor_finished(executor, success):
    if not success:
        unreal.log_error("渲染任务部分失败")
    else:
        unreal.log("所有视频渲染及数据提取任务结束！")

def render_queue_and_export_dataset():
    global _executor_ref
    unreal.EditorLoadingAndSavingUtils.load_map(MAP_PATH)
    subsystem = unreal.get_editor_subsystem(unreal.MoviePipelineQueueSubsystem)
    queue_asset = unreal.EditorAssetLibrary.load_asset(QUEUE_ASSET_PATH)

    if queue_asset:
        subsystem.load_queue(queue_asset)

    queue = subsystem.get_queue()
    
    if not os.path.exists(OUTPUT_JSON_DIR):
        os.makedirs(OUTPUT_JSON_DIR)
        
    for job in queue.get_jobs():
        name = job.job_name if job.job_name else "unnamed"
        unreal.log(f"正在渲染前提取相机数据: {name}")
        try:
            data = _sample_camera_data(job)
            if data:
                jsonl_obj = {
                    "video_id": name,
                    "video_path": f"{RENDER_OUTPUT_DIR}/{name}.mp4",
                    "frame_count": len(data),
                    "camera_trajectory": data,
                    "text_prompt": "",
                }
                out_path = os.path.join(OUTPUT_JSON_DIR, f"{_safe_filename(name)}.jsonl")
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(jsonl_obj, ensure_ascii=False) + "\n")
                unreal.log(f"成功写入数据: {out_path}")
        except Exception as e:
            unreal.log_error(f"提取数据失败: {str(e)}")

    for job in queue.get_jobs():
        cfg = job.get_configuration()
        cfg.copy_from(unreal.EditorAssetLibrary.load_asset(MRQ_CONFIG_PATH))
        job.map = unreal.SoftObjectPath(MAP_PATH)

        out_setting = cfg.find_or_add_setting_by_class(unreal.MoviePipelineOutputSetting)
        out_setting.set_editor_property("output_directory", unreal.DirectoryPath(path=RENDER_OUTPUT_DIR))
        job.set_configuration(cfg)
        
    _executor_ref = unreal.MoviePipelinePIEExecutor()
    _executor_ref.set_is_rendering_offscreen(True)
    print('is render offscreen?', _executor_ref.is_rendering_offscreen())
    _executor_ref.on_executor_finished_delegate.add_callable_unique(_on_executor_finished)
    subsystem.render_queue_with_executor_instance(_executor_ref)

if __name__ == "__main__":
    render_queue_and_export_dataset()