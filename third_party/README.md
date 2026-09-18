# 本地 CTP SDK

SDK 不属于本项目 MIT 许可证的授权范围。请自行从 SimNow 官方下载并确认使用条款；项目不提供非官方分发、自动下载或认证绕过。

当前包装层适配 CTP v6.7.13 Linux x86_64 MdApi。将合法获得的文件放入：

```
third_party/ctp/v6.7.13_linux64/
  include/ThostFtdcMdApi.h
  include/ThostFtdcUserApiDataType.h
  include/ThostFtdcUserApiStruct.h
  lib/thostmduserapi_se.so
```

该目录被 Git 忽略，但仅这些构建所需文件进入 Docker 构建上下文。镜像内包含运行所需的 SDK 库，应按 SDK 条款限制镜像的分发和访问。不要将包含 SDK 的镜像自动推送到公共镜像仓库。

本地构建：`bash scripts/build_ctp_shim.sh`。Docker 构建在 Linux amd64 环境里重新编译；不会使用宿主机上已有的 `_mdshim.so`。

早期提交曾包含 SDK 文件；本次只移除当前版本中的跟踪，不改写已发布的 Git 历史。历史版本的使用和分发仍需核对原 SDK 授权。
