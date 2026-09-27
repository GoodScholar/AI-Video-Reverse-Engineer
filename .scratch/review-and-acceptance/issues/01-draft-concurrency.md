Status: resolved
Type: task

# 防止窗口间清除或覆盖草稿

共享 localStorage 写入前需检查观察到的版本，串行化并发写入；有冲突时保留另一窗口缓存及当前编辑，明确提示处理，不静默丢失。

## Answer

已复现、修复并补回归。独立只读复核通过。验证详情见 ../report.md。
