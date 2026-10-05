# esp32-server-new

基于 [XuSenfeng/xiaozhi-esp32-server-music](https://github.com/XuSenfeng/xiaozhi-esp32-server-music) 的定制版小智服务端。

## 本版本新增/强化

- 在线点歌（JuiceMusic）：列表先选后播、歌手匹配重排、断线待选恢复
- 多平台联网搜索：B站/头条/Bing/百度等热度排序
- 歌词同步（STT 上屏不打断音乐）、仅取歌词 `get_lyrics`、歌曲信息 `song_info`
- 设备控制 `device_control`、服控 `server_control`（磁盘/内存/重启/清缓存）
- 模型切换：GLM / DeepSeek / Xiavier GPT 系列
- 记忆：manager-api 401 时回落 mem-writer 直写 MySQL

## 目录

- `main/xiaozhi-server`：服务端主代码（已同步生产热修）
- `host_tools/`：宿主机辅助（ops 网关、mem-writer、日志页）

## 注意

- 请通过环境变量 / 管理后台配置 API Key、JuiceMusic Key、manager-api secret，仓库内不含生产密钥。
- 部署请参考上游 README 与 Docker 配置。
