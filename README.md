# Keep Account Project

这是一个包含微信公众号文章图片批量下载和 OCR 识别处理的综合项目。

## 项目结构
- `download_image/`: 负责通过 playwright 下载微信公众号文章中的图片。
- `ocr_image/`: 负责利用 Tesseract 和大模型 API，对下载的图片进行 OCR 提取。

---

## 另一台设备运行说明

### 1. 拉取代码
```bash
git clone https://github.com/ghhello526/text2.git
cd text2
```

### 2. 配置与运行下载图片模块 (`download_image`)
该模块路径都采用相对路径，**无需修改任何代码路径**。
1. **安装环境**：
   ```bash
   cd download_image
   pip install -r requirements.txt
   playwright install  # 初次运行必须安装 playwright 的浏览器内核
   ```
2. **运行**：
   将要下载的微信公众号文章链接填入 `download_image/urls.txt`（一行一个），然后执行：
   ```bash
   python main.py
   ```
   下载的图片会自动保存在 `download_image/images/` 目录下。

### 3. 配置与运行 OCR 模块 (`ocr_image`)
关键的 `.env`（含云端大模型 API Key）已经随项目同步过来。
1. **安装环境**：
   直接双击运行目录下的 `setup.bat`（会自动创建虚拟环境 `.venv` 并安装依赖）。
   或者在命令行手动执行：
   ```bash
   cd ocr_image
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
2. **⚠️ 必须改动的配置**：
   使用文本编辑器打开 `ocr_image/config.yaml`，找到 `tesseract_cmd` 参数。你需要将其修改为**新设备上 Tesseract-OCR 软件的实际安装路径**：
   ```yaml
   ocr:
     local:
       # 这里必须改成新电脑上 tesseract.exe 的绝对路径
       tesseract_cmd: "D:/Software/ocr/tesseract.exe" 
   ```
   *(注：其他有关 `input_dir: "../download_image/images"` 等目录配置已经是相对路径，无需改动)*
3. **运行**：
   确保激活了虚拟环境后，可以直接处理刚才下载的图片。例如：
   ```bash
   python run.py process -f ../download_image/images/你要处理的文章名
   ```
