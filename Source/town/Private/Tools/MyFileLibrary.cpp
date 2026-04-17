// Fill out your copyright notice in the Description page of Project Settings.


#include "Tools/MyFileLibrary.h"
#include "town/Public/Tools/MyFileLibrary.h"
#include "Developer/DesktopPlatform/Public/IDesktopPlatform.h"
#include "Developer/DesktopPlatform/Public/DesktopPlatformModule.h"

void UMyFileLibrary::OpenOSFileDialog(FString& OutFilePath, bool& bSuccess)
{
	IDesktopPlatform* DesktopPlatform = FDesktopPlatformModule::Get();
	if (DesktopPlatform)
	{
		TArray<FString> OutFilenames;
		// 获取当前编辑器窗口的句柄
		const void* ParentWindowWindowHandle = FSlateApplication::Get().FindBestParentWindowHandleForDialogs(nullptr);
        
		// 弹出系统文件选择框
		bool bOpened = DesktopPlatform->OpenFileDialog(
			ParentWindowWindowHandle,
			TEXT("请选择文件"), // 窗口标题
			TEXT(""),          // 默认打开的路径
			TEXT(""),          // 默认文件名
			TEXT("All Files (*.*)|*.*"), // 文件类型过滤（比如 "JSON Files (*.json)|*.json"）
			EFileDialogFlags::None,
			OutFilenames
		);

		if (bOpened && OutFilenames.Num() > 0)
		{
			OutFilePath = OutFilenames[0];
			bSuccess = true;
			return;
		}
	}
	bSuccess = false;
}

FString UMyFileLibrary::ConvertToAssetPath(const FString& PhysicalPath)
{
	FString OutPackageName;
	if (FPackageName::TryConvertFilenameToLongPackageName(PhysicalPath, OutPackageName))
	{
		return OutPackageName; // 转换成功，返回干净的路径
	}
    
	// 如果转换失败（比如选了非工程目录下的文件），返回空字符串
	return TEXT(""); 
}
