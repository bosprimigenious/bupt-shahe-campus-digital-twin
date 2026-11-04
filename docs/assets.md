# 场景资产契约

源码中不包含真实资产。`scripts/serve.py --asset-root`需要一个已合法取得的资源目录；服务只读取下表列出的12个文件，其余内容不会作为资产接口提供。

| 浏览器请求名 | 相对资源根目录的文件 |
|---|---|
| `campus_data.json` | `outputs/campus_student_flow/campus_data.json` |
| `campus_agents.json` | `outputs/campus_student_flow/agents.json` |
| `library_agents.json` | `outputs/student_flow/agents.json` |
| `layout.json` | `outputs/student_flow/layout.json` |
| `Campus_Exterior.glb` | `outputs/campus_student_flow/Campus_Exterior.glb` |
| `Campus_Vehicles_Animated.glb` | `outputs/campus_student_flow/Campus_Vehicles_Animated.glb` |
| `Students_Animated.glb` | `outputs/student_flow/Students_Animated.glb` |
| `Library_Floor_1.glb`至`Library_Floor_5.glb` | `outputs/student_flow/`下的对应文件 |

JSON描述建筑、道路、POI、轨迹和座位布局。人物ID是模拟样本标识，不应替换为真实学生的个人信息。不能将现场数据直接写入源码或示例文件。

GLB采用glTF 2.0，单位为米、Y轴向上，缓冲与贴图应内嵌。人物和车辆模型各需唯一的`_defaultAnim_`动画片段；楼层GLB只包含对应静态馆体。不要混用已合并静态人物的楼层快照，否则会重复显示人物。

轨迹JSON使用Blender的Z-up坐标；查看器转换为`(x,z,-y)`。馆内模型与轨迹在内存中使用同一校园偏移，原资源不被改写。展开视图只添加观察用楼层间距，不能用于测量实际层高。

当前人数、模型和布局契约沿用既有校园资源，未实现任意校园文件的自动适配。输入文件存在、GLB可解析或动画片段存在，都不能替代实际画面的比例、贴地、导航与切层检查。
