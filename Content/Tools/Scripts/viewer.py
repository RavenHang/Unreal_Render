import unreal

def pick_level_sequence_dialog():
    # 并通过 unreal.LevelSequence 类自动过滤，只显示 Level Sequence 资产
    selected_sequence = unreal.LevelSequence.cast(unreal.AssetDialog.open_object_picker("请选择基础 Level Sequence", "/Game", unreal.LevelSequence))
    print('debug')
    if selected_sequence:
        # 如果用户选中了东西并点击确定，返回该资产的 Object Path
        print(selected_sequence.get_path_name())
        return selected_sequence.get_path_name()

    # 如果用户取消，返回空字符串
    return ""