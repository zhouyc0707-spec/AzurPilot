"""临时脚本：确认 rich 版本与 render_many 行为（用完即删）。"""
from importlib.metadata import version

from rich.text import Text

from module.webui.widgets import RichLog

print('  rich 版本:', version('rich'))
print('  textual 版本:', version('textual'))

log = RichLog("log")
html = log.render_many((Text("第一行"), Text("第二行")))
print('  render_many 输出长度:', len(html))
print('  含「第一行」:', "第一行" in html)
print('  console.record:', log.console.record)
print('  片段:', html[:160].replace('\n', ' '))
