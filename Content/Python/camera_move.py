import unreal
import json
import os
import random
import math


def _sample_front_arc_offset_cm(
    forward_vec,
    min_distance_cm=500.0,   # 5m
    max_distance_cm=1000.0,  # 10m
    max_arc_deg=60.0,        # 总弧度 <= 60°
):
    """
    在角色前方水平扇形内采样偏移（世界 XY 平面）。
    方向以角色 forward 为中心，左右各 max_arc_deg/2。
    返回世界坐标偏移向量（Z=0）。
    """
    # 归一化前向，并投影到水平面
    f = unreal.Vector(forward_vec.x, forward_vec.y, 0.0)
    length = math.sqrt(f.x * f.x + f.y * f.y)
    if length < 1e-6:
        # 兜底：角色前向异常时，默认 +X
        f = unreal.Vector(1.0, 0.0, 0.0)
    else:
        f = unreal.Vector(f.x / length, f.y / length, 0.0)

    base_yaw_deg = math.degrees(math.atan2(f.y, f.x))
    half_arc = max_arc_deg * 0.5
    yaw_offset_deg = random.uniform(-half_arc, half_arc)
    yaw_deg = base_yaw_deg + yaw_offset_deg
    yaw_rad = math.radians(yaw_deg)

    dist = random.uniform(min_distance_cm, max_distance_cm)
    x = dist * math.cos(yaw_rad)
    y = dist * math.sin(yaw_rad)
    return unreal.Vector(x, y, 0.0)


def _clamp_rotator_pitch(rot, limit_deg=45.0):
    if rot.pitch > limit_deg:
        rot.pitch = limit_deg
    elif rot.pitch < -limit_deg:
        rot.pitch = -limit_deg
    return rot


def _normalize_angle_deg(angle):
    # 归一到 [-180, 180)
    return (angle + 180.0) % 360.0 - 180.0


def create_sequence_from_data(
    json_path,
    sequence_name,
    package_path="/Game/Cinematics",
    target_tag="FocusTarget",
    anim_path="",
):
    """
    根据 JSON 数据生成 Sequencer，并在当前关卡中寻找指定 Tag 的单位加入 Sequencer。

    若找到目标：
    1) 相机初始点采样在角色“正前方”扇形区域（距离 5m~10m，总弧度 <= 60°）
    2) 将整条 JSON 轨迹按位置 delta 平移到该起点
    3) 将整条 JSON 旋转按首帧旋转 delta 叠加（相对旋转，而非绝对旋转）
    """
    if not os.path.exists(json_path):
        unreal.log_error(f"找不到数据文件: {json_path}")
        return

    with open(json_path, "r", encoding="utf-8-sig") as file:
        camera_data = json.load(file)

    if not camera_data:
        unreal.log_error("JSON 数据为空！")
        return

    # 统一按 frame 排序，避免输入顺序导致时间轴异常
    camera_data = sorted(camera_data, key=lambda d: d.get("frame", 0))

    min_frame = camera_data[0].get("frame", 0)
    max_frame = camera_data[-1].get("frame", 0)
    end_frame = max_frame + 1

    # 找目标角色（兼容当前 UE Python：不使用 unreal.FName）
    actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    all_actors = actor_subsystem.get_all_level_actors()

    target_actor = None
    for actor in all_actors:
        if actor.actor_has_tag(target_tag):
            target_actor = actor
            break

    # 默认：使用 JSON 首帧位姿
    spawn_location = unreal.Vector(
        camera_data[0]["x"],
        camera_data[0]["y"],
        camera_data[0]["z"],
    )
    spawn_rotation = unreal.Rotator(
        camera_data[0].get("roll", 0.0),
        camera_data[0].get("pitch", 0.0),
        camera_data[0].get("yaw", 0.0),
    )

    # 位置/旋转增量（默认不偏移）
    delta = unreal.Vector(0.0, 0.0, 0.0)
    rot_delta_roll = 0.0
    rot_delta_pitch = 0.0
    rot_delta_yaw = 0.0

    if target_actor:
        char_loc = target_actor.get_actor_location()
        char_forward = target_actor.get_actor_forward_vector()

        # 采样角色正前方扇形：5m~10m，弧度<=60°
        offset = _sample_front_arc_offset_cm(
            char_forward,
            min_distance_cm=500.0,   # 5m
            max_distance_cm=1000.0,  # 10m
            max_arc_deg=60.0,
        )

        # 保持与角色同高（如需抬高相机，可自行加固定 z 偏移）
        spawn_location = unreal.Vector(char_loc.x + offset.x, char_loc.y + offset.y, char_loc.z)

        look_at_rot = unreal.MathLibrary.find_look_at_rotation(spawn_location, char_loc)
        spawn_rotation = _clamp_rotator_pitch(look_at_rot, limit_deg=45.0)

        # 位置增量（整条轨迹平移）
        orig_start_loc = unreal.Vector(
            camera_data[0]["x"],
            camera_data[0]["y"],
            camera_data[0]["z"],
        )
        delta = spawn_location - orig_start_loc

        # 旋转增量（整条轨迹相对旋转）
        orig_start_rot = unreal.Rotator(
            camera_data[0].get("roll", 0.0),
            camera_data[0].get("pitch", 0.0),
            camera_data[0].get("yaw", 0.0),
        )
        rot_delta_roll = _normalize_angle_deg(spawn_rotation.roll - orig_start_rot.roll)
        rot_delta_pitch = _normalize_angle_deg(spawn_rotation.pitch - orig_start_rot.pitch)
        rot_delta_yaw = _normalize_angle_deg(spawn_rotation.yaw - orig_start_rot.yaw)

        unreal.log(
            f"已采样角色前方扇形起点并应用相对位姿偏移，frame={min_frame} 从随机位姿直接开始 dolly_in。"
        )
    else:
        unreal.log_warning(
            f"未找到 Tag 为 '{target_tag}' 的目标：将使用 JSON 原始轨迹，不做随机采样。"
        )

    # 创建 Sequence
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    factory = unreal.LevelSequenceFactoryNew()
    sequence = asset_tools.create_asset(sequence_name, package_path, unreal.LevelSequence, factory)

    if not sequence:
        unreal.log_error(
            f"创建 Sequencer 失败！请检查 {package_path} 目录下是否已存在同名资源。"
        )
        return

    sequence.set_display_rate(unreal.FrameRate(15, 1))

    # 生成相机（初始位姿先放到 spawn，最终由关键帧驱动）
    editor_level_lib = unreal.EditorLevelLibrary
    camera_actor = editor_level_lib.spawn_actor_from_class(
        unreal.CineCameraActor,
        spawn_location,
        spawn_rotation,
    )
    if not camera_actor:
        unreal.log_error("无法在关卡中生成摄像机！请确认当前关卡有效。")
        return

    camera_actor.set_actor_label("Procedural_CineCamera")
    camera_binding = sequence.add_possessable(camera_actor)
    camera_cut_track = sequence.add_track(unreal.MovieSceneCameraCutTrack)

    camera_cut_section = camera_cut_track.add_section()

    camera_cut_section.set_range(min_frame, end_frame)
    cut_section = unreal.MovieSceneCameraCutSection.cast(camera_cut_section)
    cut_section = unreal.MovieSceneCameraCutSection.cast(camera_cut_section)

    if cut_section:
        # --- 核心修改部分 ---
        # 创建 BindingID 结构体对象
        binding_id = unreal.MovieSceneObjectBindingID()
        
        binding_id.set_editor_property("guid", camera_binding.get_id())
        
        cut_section.set_camera_binding_id(binding_id)
        # ------------------
    else:
        unreal.log_warning("无法获取有效的 MovieSceneCameraCutSection。")
    
    transform_track = camera_binding.add_track(unreal.MovieScene3DTransformTrack)
    transform_section = transform_track.add_section()
    transform_section.set_range(min_frame, end_frame)

    camera_comp = camera_actor.get_cine_camera_component()
    camera_component_binding = sequence.add_possessable(camera_comp)

    focal_length_track = camera_component_binding.add_track(unreal.MovieSceneFloatTrack)
    focal_length_track.set_property_name_and_path("CurrentFocalLength", "CurrentFocalLength")
    focal_section = focal_length_track.add_section()
    focal_section.set_range(min_frame, end_frame)

    transform_channels = transform_section.get_all_channels()
    loc_x, loc_y, loc_z = transform_channels[0], transform_channels[1], transform_channels[2]
    rot_roll, rot_pitch, rot_yaw = transform_channels[3], transform_channels[4], transform_channels[5]
    focal_channel = focal_section.get_all_channels()[0]

    unreal.log("开始写入摄像机关键帧...")

    for data in camera_data:
        frame = data.get("frame", 0)
        frame_num = unreal.FrameNumber(frame)

        # 1) 位置：整条轨迹相对平移
        x = data["x"] + delta.x
        y = data["y"] + delta.y
        z = data["z"] + delta.z

        # 2) 旋转：整条轨迹相对叠加
        base_roll = data.get("roll", 0.0)
        base_pitch = data.get("pitch", 0.0)
        base_yaw = data.get("yaw", 0.0)

        roll = _normalize_angle_deg(base_roll + rot_delta_roll)
        pitch = _normalize_angle_deg(base_pitch + rot_delta_pitch)
        yaw = _normalize_angle_deg(base_yaw + rot_delta_yaw)

        # 保持约束：俯仰角不超过 45 度
        pitch = max(-45.0, min(45.0, pitch))

        loc_x.add_key(frame_num, x)
        loc_y.add_key(frame_num, y)
        loc_z.add_key(frame_num, z)
        rot_roll.add_key(frame_num, roll)
        rot_pitch.add_key(frame_num, pitch)
        rot_yaw.add_key(frame_num, yaw)

        if "focal_length" in data:
            focal_channel.add_key(frame_num, data["focal_length"])

    # 绑定角色和可选动画
    if target_actor:
        unreal.log(f"🎯 成功找到场景单位: {target_actor.get_actor_label()}，正在添加到 Sequencer...")
        character_binding = sequence.add_possessable(target_actor)

        if anim_path:
            anim_sequence = unreal.EditorAssetLibrary.load_asset(anim_path)
            if anim_sequence:
                anim_track = character_binding.add_track(unreal.MovieSceneSkeletalAnimationTrack)
                anim_section = anim_track.add_section()
                anim_section.set_range(min_frame, end_frame)
                anim_section.params.animation = anim_sequence
                unreal.log(f"已为 {target_actor.get_actor_label()} 绑定动画资产。")
            else:
                unreal.log_warning("⚠️ 无法加载指定的动画资产，请检查 anim_path。")
    else:
        unreal.log_warning(f"⚠️ 在场景中未找到带有 Tag '{target_tag}' 的单位。跳过目标绑定。")

    # 播放范围从最小帧开始，避免 0->min_frame 的空段/跳变观感
    sequence.set_playback_start(min_frame)
    sequence.set_playback_end(end_frame)

    unreal.LevelSequenceEditorBlueprintLibrary.refresh_current_level_sequence()

    save_success = unreal.EditorAssetLibrary.save_loaded_asset(sequence)
    if save_success:
        unreal.log(f"成功创建并保存 Sequencer: {package_path}/{sequence_name}")
    else:
        unreal.log_warning(f"Sequencer 创建成功，但自动保存失败。请手动在内容浏览器中保存 {sequence_name}。")

def batch_create_sequences_from_dolly_in(
    dolly_in_folder=None,
    anim_folder_path="/Game/Characters/Mannequins/Anims/Test",
    sequence_base_path="/Game/Cinematics",
    target_tag="Actor",
):
    """
    批量读取 dolly_in 文件夹下所有 JSON 文件 和 /Game/Characters/Mannequins/Anims 下所有动画，
    生成所有组合的 Sequence，命名为 scene1_{json_name_no_ext}_{anim_name}。
    
    :param dolly_in_folder: JSON 文件夹路径（默认为 Python 脚本同级目录的 dataset/dolly_in）
    :param anim_folder_path: UE 资产路径中的动画文件夹
    :param sequence_base_path: 生成的 Sequence 保存路径
    :param target_tag: 目标角色 Tag
    """
    if dolly_in_folder is None:
        # 获取脚本所在目录的 dataset/dolly_in 文件夹
        script_dir = os.path.dirname(__file__)
        dolly_in_folder = os.path.join(script_dir, "dataset", "dolly_in")
    
    if not os.path.exists(dolly_in_folder):
        unreal.log_error(f"dolly_in 文件夹不存在: {dolly_in_folder}")
        return
    
    # 1. 收集所有 JSON 文件
    json_files = []
    for file_name in os.listdir(dolly_in_folder):
        if file_name.endswith(".json"):
            json_path = os.path.join(dolly_in_folder, file_name)
            json_files.append((file_name[:-5], json_path))  # (name_no_ext, full_path)
    
    if not json_files:
        unreal.log_warning(f"在 {dolly_in_folder} 中未找到 JSON 文件")
        return
    
    unreal.log(f"找到 {len(json_files)} 个 JSON 文件")
    
    # 2. 使用 EditorAssetLibrary 遍历动画资产文件夹
    anim_assets = unreal.EditorAssetLibrary.list_assets(anim_folder_path, recursive=False)
    if not anim_assets:
        unreal.log_warning(f"在 {anim_folder_path} 中未找到动画资产")
        return
    
    # 筛选出实际的动画资产（排除文件夹等）
    valid_anims = []
    for anim_path in anim_assets:
        # 加载资产检查是否有效
        anim_asset = unreal.EditorAssetLibrary.load_asset(anim_path)
        if anim_asset and "Anim" in str(type(anim_asset)):
            # 获取资产名称（不含路径）
            raw_name = anim_path.split("/")[-1]      # 例如 punching_bag.punching_bag
            anim_name = raw_name.split(".")[0]       # -> punching_bag
            valid_anims.append((anim_name, anim_path))
    
    if not valid_anims:
        unreal.log_warning(f"在 {anim_folder_path} 中未找到有效的动画资产")
        return
    
    unreal.log(f"找到 {len(valid_anims)} 个动画资产")
    
    # 3. 双重循环生成所有组合
    total = len(json_files) * len(valid_anims)
    count = 0
    
    for json_name, json_path in json_files:
        for anim_name, anim_path in valid_anims:
            count += 1
            # 命名规则：scene1_{json_name}_{anim_name}
            sequence_name = f"{json_name}-{anim_name}"
            
            unreal.log(f"[{count}/{total}] 生成 Sequence: {sequence_name}")
            
            try:
                create_sequence_from_data(
                    json_path,
                    sequence_name,
                    package_path=sequence_base_path,
                    target_tag=target_tag,
                    anim_path=anim_path,
                )
            except Exception as e:
                unreal.log_error(f"生成 {sequence_name} 失败: {str(e)}")
    
    unreal.log(f"✅ 批量生成完成！共生成 {total} 个 Sequence。")