# 页面人气榜 Top 3 扫描器（已归档）

该工具每小时整点后 5 秒读取 B 站页面人气榜前三名，转换为直播间 ID 后输出，并可沿用项目根目录 `config.txt` 中的 Discord 通知配置。

它依赖 Playwright，已不属于主监视器的安装内容。

## 安装与运行

在本目录双击 `install.bat`，安装 Python 依赖和 Chromium；随后双击 `scan_top3.bat`。

也可以手动执行：

```bat
pip install -r requirements.txt
playwright install chromium
python scan_top3.py
```
