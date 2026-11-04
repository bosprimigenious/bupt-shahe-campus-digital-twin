# 沙河校园场景查看器

使用 Three.js 展示校园建筑、人群轨迹、车辆动画和五层图书馆。项目将校园总览与图书馆室内回放放在同一界面，支持人物跟随、楼层筛选和时间控制。

**本仓库提供查看器源码，不附带校园模型或运行数据。** 真实场景资产涉及地图数据、外部模型与参考材料，尚未完成独立再分发许可确认。克隆源码后需要自行准备有权使用的资产，才能显示校园；无需资产即可运行服务器与隐私检查的测试。

## 功能

- 校园总览与图书馆1—5层切换，支持将五层拉开展示。
- 校园120人／1,000人两档展示。千人模式复用基础轨迹，通过确定性时间错位安排位置。
- 图书馆播放已有GLB中的人物动作；轨迹JSON提供人物状态和点选信息。
- 车辆GLB动画、播放／暂停、时间轴、调速与循环回放。
- 点击人物查看状态，近距离观察或跟随；小地图可显示当前模拟样本的密度。

这些是预设轨迹与模型动画，不是实时客流、独立多智能体决策或交通避碰仿真。校园与馆内使用两组样本，没有跨场景身份接续。

## 运行

需要Python 3.10或更新版本，以及支持WebGL2的桌面浏览器。服务端只使用Python标准库；Three.js固定版本0.180.0已放在本地，不需要Node、npm或在线CDN来运行查看器。

```sh
git clone https://github.com/bosprimigenious/bupt-shahe-campus-digital-twin.git
cd bupt-shahe-campus-digital-twin
python3 scripts/serve.py --asset-root /path/to/scene-resources --open
```

`--asset-root`指向已准备好的资源根目录，目录结构见[资产契约](docs/assets.md)。启动前会检查12个必需文件。服务固定绑定本机回环地址，默认端口8768；可用`--port`修改端口。

只检查资源、不启动服务：

```sh
python3 scripts/serve.py --asset-root /path/to/scene-resources --check-only
```

浏览器打开后，左键拖动旋转、滚轮缩放、右键平移。右侧“场景”切换校园或楼层；“图书馆人群漫游”进入行走人物近景，“五层展开”用于观察馆内布局。底部控制播放与时间，选中人物后可近看或跟随。

## 目录

```text
web/                    查看器、轨迹采样、Three.js及其许可证
scripts/serve.py        只读本地服务与资产检查
scripts/audit_public.py 公开前的文件与Git历史检查
tests/                  HTTP边界、隐私检查与可选浏览器验收
docs/assets.md          输入文件、坐标和动画约定
docs/public-release.md  公开范围与发布前检查
LICENSE                 本项目自有代码的MIT许可证
NOTICE.md               第三方内容与资产的许可边界
```

## 验证

以下测试使用临时合成数据，不需要校园模型：

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/audit_public.py --history
```

实际画面的浏览器检查需要合法取得的完整资产、本机Chrome和额外的Playwright Core工具。启动本地服务后，可运行：

```sh
PLAYWRIGHT_MODULE=/path/to/playwright-core node tests/verify_viewer.cjs
```

可通过`CHROME_EXECUTABLE`指定Chrome可执行文件，`CAMPUS_QA_DIR`指定报告目录。该检查覆盖人群实际移动、五层筛选、整馆展开、暂停拖动、循环和返回校园后的显示状态。Playwright是开发验收工具，不是查看器的运行依赖。

## 已知边界

校园建筑高度和室内布局含估算；五层展开的额外间距只是观察手段。人物与车辆没有避碰，密集路线可能重叠；性能取决于设备和视角。

本地查看器与森园区运行时是不同链路。TJS不会自动携带此处的面板、人物跟随和统一时间轴。已有平台场景仍有楼层相机取景、人物高度问题，完整平台验收尚未通过；本仓库不包含平台地址、登录方式或设备控制脚本。

## 许可与公开范围

自有源码使用[MIT许可证](LICENSE)，Three.js保留其[原MIT许可证](web/vendor/three/LICENSE)。模型、PPT、照片、预览渲染、真实运行JSON、平台原始响应、历史备份与许可不明的下载模块不在此源码发行范围内。详见[第三方声明](NOTICE.md)。

仓库目前保持私有，处于开源前准备阶段。清理源码不等于取得未包含资产的再分发权，也不代表模型平台验收完成。
