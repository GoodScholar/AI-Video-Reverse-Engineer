# 使用 React 前端与 Python 本地分析服务

MVP 将界面与本地分析能力分为 React + TypeScript 前端和 Python + FastAPI 服务。相比全部使用 Node.js，这让 FFmpeg、OpenCV 和视频分析库的接入更直接；相比纯 Python 页面，它更适合承载多步骤、可编辑的产品界面。代价是需要维护两个运行时，具体版本与进程管理方式将在实施计划中锁定。
