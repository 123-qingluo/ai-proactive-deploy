"""宿主适配器示例。部署后请把 create_host 换成你的真实 IM 投递。

约定：
- send_text(text, strategy) 必须返回 True 才算投递成功；None/False 都按失败处理。
- recall / persona / audit 都是可选的；失败不能影响主循环。
"""
from proactive_core.host import FileSinkHost


def create_host(root, config):
    # 默认：只写 data/outbox.jsonl，不连任何 IM。生产请替换。
    return FileSinkHost(root)
