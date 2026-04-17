import unreal



def create_sequence_variant(base_asset_path, speed_multiplier=1.0, fl_multiplier=1.0):
    """
    基于基础运镜，生成一个修改了速度和焦距倍率的新版本
    :param base_asset_path: 基础资产路径
    :param speed_multiplier: 速度倍率 (例如 2.0 表示快放2倍，时间缩短一半)
    :param fl_multiplier: 焦距倍率 (例如 1.5 表示所有焦距数值乘 1.5)
    """
    speed_multiplier = float(speed_multiplier)
    fl_multiplier = float(fl_multiplier)

    if speed_multiplier <= 0 or fl_multiplier <= 0:
        unreal.log_error("⚠️ 速度倍率和焦距倍率必须大于 0！")
        return ""

    if not unreal.EditorAssetLibrary.does_asset_exist(base_asset_path):
        unreal.log_error(f"⚠️ 找不到基础资产: {base_asset_path}")
        return ""

    folder_path = base_asset_path.rsplit('/', 1)[0]
    asset_name = base_asset_path.split('/')[-1]
    
    safe_speed = str(speed_multiplier).replace('.', '_')
    safe_fl = str(fl_multiplier).replace('.', '_')
    
    name_parts = [asset_name]
    if speed_multiplier != 1.0:
        name_parts.append(f"Spd_{safe_speed}x")
    if fl_multiplier != 1.0:
        name_parts.append(f"FL_{safe_fl}x")
        
    # 如果两个倍率都是 1.0，加个 Variant 后缀防重名
    if len(name_parts) == 1:
        name_parts.append("Variant") 
        
    new_asset_name = "_".join(name_parts)
    new_asset_path = f"{folder_path}/{new_asset_name}"

    # 清理旧文件并复制新文件
    if unreal.EditorAssetLibrary.does_asset_exist(new_asset_path):
        unreal.EditorAssetLibrary.delete_asset(new_asset_path)

    new_seq_obj = unreal.EditorAssetLibrary.duplicate_asset(base_asset_path, new_asset_path)
    if not new_seq_obj:
        unreal.log_error("⚠️ 复制资产失败！")
        return ""

    sequence = unreal.LevelSequence.cast(new_seq_obj)
    unreal.log(f"开始处理序列变体: {new_asset_name}")

    if speed_multiplier != 1.0:
        start_frame = sequence.get_playback_start()
        end_frame = sequence.get_playback_end()
        sequence.set_playback_start(int(start_frame / speed_multiplier))
        sequence.set_playback_end(int(end_frame / speed_multiplier))

    bindings = sequence.get_bindings()
    
    for binding in bindings:
        for track in binding.get_tracks():
            # 使用 str() 强制转换 FName 为字符串
            property_name = str(track.get_property_name()) if hasattr(track, 'get_property_name') else ""
            
            if property_name == "CurrentFocalLength":             
                sections = track.get_sections()
                for section in sections:
                    channels = section.get_all_channels()
                    for channel in channels:
                        # 兼容单精度与双精度浮点通道
                        if isinstance(channel, (unreal.MovieSceneScriptingDoubleChannel, unreal.MovieSceneScriptingFloatChannel)):
                            keys = channel.get_keys()
                            
                            # 如果已有关键帧，遍历并乘以倍率
                            if keys:
                                for key in keys:
                                    current_val = key.get_value()
                                    new_val = current_val * fl_multiplier
                                    key.set_value(new_val) 
                                unreal.log(f"  - 已将现有的 {len(keys)} 个焦距关键帧数值乘以倍率 {fl_multiplier}。")
                            
                            # 如果没有关键帧，在起点添加一个 (需要给个基础默认值，比如 35.0)
                            else:
                                if section.has_start_frame():
                                    start_frame = section.get_start_frame()
                                    key_time = unreal.FrameNumber(start_frame) if start_frame is not None else unreal.FrameNumber(0)
                                else:
                                    key_time = unreal.FrameNumber(0)
                                    
                                # 假设当前镜头的默认焦距是 35.0 (你可以根据项目需求改成其他的，比如 50.0)
                                default_base_fl = 35.0
                                new_val = default_base_fl * fl_multiplier
                                
                                key = channel.add_key(key_time, new_val)
                                unreal.log(f"  - 轨道无关键帧，已在开头添加关键帧: 基础值 {default_base_fl} * 倍率 {fl_multiplier} = {new_val}")
    
    unreal.EditorAssetLibrary.save_asset(new_asset_path)
    unreal.log(f"🎉 成功！已生成全新的变体版本: {new_asset_path}")
    
    return new_asset_path