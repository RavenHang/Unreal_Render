#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "MyFileLibrary.generated.h" 

/**
 * 暴露给蓝图的操作系统文件处理库
 */
UCLASS()
class TOWN_API UMyFileLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// UFUNCTION 宏让蓝图能搜到这个节点，BlueprintCallable 表示可在蓝图执行
	UFUNCTION(BlueprintCallable, Category = "File Browser")
	static void OpenOSFileDialog(FString& OutFilePath, bool& bSuccess);
	
	UFUNCTION(BlueprintPure, Category = "File Browser")
	static FString ConvertToAssetPath(const FString& PhysicalPath);
};