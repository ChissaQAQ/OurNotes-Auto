"""MaaFramework Agent：让 MFAAvalonia / MXU 等通用界面调用本工具。

界面按 ``interface.json`` 启动 ``agent/main.py``，任务节点执行自定义动作 ``OurNotesRun``。
动作把任务名、界面选项（写在参数节点的 ``attach`` 里）和界面连接的模拟器交给一个
``python -m ournotes_auto agent-run`` 子进程，由它换成命令行参数完成演奏，Agent 把子进程的日志转发到界面。

之所以另起子进程：Agent 进程里 MaaFramework 处于 Agent 服务模式，
不能在本地创建 Resource / Tasker，导航用的 OCR 无法运行。
"""
