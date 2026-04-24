# 图标生成指南(Tauri 桌面 + Web favicon)

> **文件定位**:
> - `packages/desktop/tauri/icons/` — Tauri 桌面 / iOS / Android 图标
> - `packages/app/public/` — Web 应用 favicon(Vite 用)
> **面向**:换 logo 时 / 第一次打包发 release 前。
> **源文件**:`assets/logo.svg`(项目主 logo,矢量源);`assets/logo-icon.png`(从 SVG 光栅化出的 1024×1024 中间产物,被 `tauri icon` 吃)。

---

## 一图流

| 产物 | 作用 | 必填? |
|---|---|---|
| `icon.ico` | Windows exe 图标 · 任务栏 · 资源管理器 · 安装包 | ✅ 桌面必须 |
| `icon.icns` | macOS 应用图标 | macOS 打包时 |
| `icon.png` | Linux / 兜底 | Linux 打包时 |
| `32x32.png` / `128x128.png` / `128x128@2x.png` | 多尺寸 PNG,窗口顶栏 / 系统托盘 | ✅ Tauri runtime 要 |
| `Square*Logo.png` × 9 + `StoreLogo.png` | Windows Store / MSIX 专用尺寸 | 走 MSIX 才用 |
| `android/` + `ios/` | 移动端启动图(mobile build) | v0.1 不用 |

v0.1 桌面场景,**`tauri.conf.json` 的 `bundle.icon` 只需 5 条**(32/128/256/icns/ico);其余文件生成出来留着,占不了多少空间。

---

## 一、源文件与中间产物

本项目 logo 的真源是 **矢量 SVG** `assets/logo.svg`(见 `assets/logo-design.md` 的元素拆解)。Tauri / Web favicon 光栅化都从这一个源出发:

```
assets/logo.svg  (矢量源 · 人工维护)
      │
      │  resvg-py(SVG → PNG,Rust 后端,无 cairo 依赖)
      ▼
assets/logo-icon.png  (1024×1024 · 给 tauri icon 吃)
      │
      ├──► packages/desktop/tauri/icons/*          (tauri icon 生成全套)
      └──► packages/app/public/favicon.{svg,ico,png}  (Web favicon 直接从 SVG 渲)
```

**源 PNG 要求**(给 Tauri CLI 用):

| 属性 | 要求 | 说明 |
|---|---|---|
| 尺寸 | **1024×1024** | 源 SVG viewBox 是 256×256,4 倍渲染既够清晰又不过度 |
| 比例 | **方形 1:1** | 非方形会被 Tauri CLI 拒 / 强裁 |
| 背景 | 透明 RGBA | macOS / iOS 会自动加圆角,源图别自己加 |
| 格式 | `.png` | Tauri CLI 官方支持;SVG 不被直接接受 |

---

## 二、生成命令

### 2.1 SVG → logo-icon.png(1024×1024)

前置:`pip install resvg-py`(Rust 绑定的 resvg,一次装好永久用,无 cairo 原生依赖)。

在 repo 根跑:

```bash
python -c "
import resvg_py
from pathlib import Path
svg = Path('assets/logo.svg').read_text(encoding='utf-8')
png = bytes(resvg_py.svg_to_bytes(svg_string=svg, width=1024, height=1024))
Path('assets/logo-icon.png').write_bytes(png)
print('wrote assets/logo-icon.png')
"
```

### 2.2 Tauri 全套桌面 / iOS / Android 图标

```bash
cd packages/desktop
bunx @tauri-apps/cli icon ../../assets/logo-icon.png --output tauri/icons
```

说明:
- `bunx @tauri-apps/cli icon` 不依赖 `bun install`(临时拉 CLI 执行),方便 CI / 干净环境
- 装过依赖也可以用 `bun --filter=@chariot/desktop tauri icon ...` 跑,等价

跑完会刷出一长串 "iOS Creating ..." / "Android Creating ..." / "Desktop Creating ...",最后 `Exited with code 0` 就是成功。

### 2.3 Web favicon(Vite app)

Vite 默认从 `packages/app/public/` 挂根路径静态资源。这里需要:

| 文件 | 尺寸 | 用途 |
|---|---|---|
| `favicon.svg` | — | 现代浏览器首选(矢量,深浅主题自适应) |
| `favicon.ico` | 16 + 32 + 48 多嵌 | 老浏览器 / Windows 任务栏 |
| `apple-touch-icon.png` | 180×180 | iOS 添加到主屏幕 |
| `favicon-16.png` / `favicon-32.png` | 16, 32 | 备选 PNG |

一条命令全生成:

```bash
python -c "
import resvg_py, shutil
from io import BytesIO
from pathlib import Path
from PIL import Image

svg_path = Path('assets/logo.svg')
out = Path('packages/app/public'); out.mkdir(parents=True, exist_ok=True)
svg = svg_path.read_text(encoding='utf-8')

def render(n):
    return Image.open(BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=svg, width=n, height=n))))

sizes = [(16,16), (32,32), (48,48)]
imgs = [render(s[0]) for s in sizes]
imgs[0].save(out/'favicon.ico', format='ICO', sizes=sizes, append_images=imgs[1:])
render(180).save(out/'apple-touch-icon.png')
render(16).save(out/'favicon-16.png')
render(32).save(out/'favicon-32.png')
shutil.copy(svg_path, out/'favicon.svg')
print('wrote packages/app/public/{favicon.svg, favicon.ico, apple-touch-icon.png, favicon-{16,32}.png}')
"
```

`packages/app/index.html` 的 `<head>` 里要有:

```html
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<link rel="alternate icon" type="image/x-icon" href="/favicon.ico" />
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
```

### 替代:只替换 `icon.ico`(紧急用)

手头只有现成的 .ico,不想跑 CLI,就**直接覆盖**:

```
packages/desktop/tauri/icons/icon.ico
```

前提是这个 .ico **内嵌多尺寸**(至少 256/48/32/16),否则 Windows 大图标处会糊。

---

## 三、引用:更新 `tauri.conf.json`

```json
"bundle": {
  "icon": [
    "icons/32x32.png",
    "icons/128x128.png",
    "icons/128x128@2x.png",
    "icons/icon.icns",
    "icons/icon.ico"
  ]
}
```

路径相对 `tauri.conf.json` 所在目录(即 `packages/desktop/tauri/`)。

桌面场景就这 5 条;MSIX / mobile 要再加对应文件。

---

## 四、验证

换完图标后:

```bash
# 1. cargo check 确认 tauri-build 能识别 icon
export PATH="$HOME/.cargo/bin:$PATH"  # (如果当前 shell 没 cargo)
cd packages/desktop/tauri && cargo check

# 2. dev 起窗口看效果
bun --filter=@chariot/desktop dev
```

窗口顶栏图标 + 任务栏图标 + exe 属性里的图标都应该变成新 logo。

---

## 五、常见坑

| 症状 | 原因 | 解决 |
|---|---|---|
| `icons/icon.ico not found` | tauri-build 找不到 .ico,生成不了 Windows PE Resource | `tauri icon` 跑完,或手放一个 .ico 到 `packages/desktop/tauri/icons/icon.ico` |
| `Image must be square` | 源 PNG 非 1:1 | 裁剪或加透明 padding 成方形 |
| 生成后大图标糊 | 源 PNG 太小(< 256)被升采样 | 用 512 或 1024 的源 |
| `tauri.conf.json` 里 bundle.icon 路径错 | 路径相对 `tauri.conf.json` 所在目录 | 用 `icons/xxx.png`,不要写 `tauri/icons/xxx.png` |
| Tauri dev 窗口图标没变 | 改了 conf 但 tauri dev 没重启 | `Ctrl+C` 停,再 `bun run dev` |

---

## 六、目录清单(生成后)

```
packages/desktop/tauri/icons/
├── icon.ico              # Windows · 多尺寸嵌入
├── icon.icns             # macOS
├── icon.png              # 1024 · 兜底
├── 32x32.png
├── 64x64.png
├── 128x128.png
├── 128x128@2x.png        # 256
├── Square30x30Logo.png   # Windows Store / MSIX
├── Square44x44Logo.png
├── Square71x71Logo.png
├── Square89x89Logo.png
├── Square107x107Logo.png
├── Square142x142Logo.png
├── Square150x150Logo.png
├── Square284x284Logo.png
├── Square310x310Logo.png
├── StoreLogo.png
├── android/              # mipmap-hdpi/mdpi/xhdpi/xxhdpi/xxxhdpi 下的 ic_launcher*.png
└── ios/                  # AppIcon 各种尺寸
```

全部 **commit**(icons 是代码产物,需要入版本库);`.gitignore` 没排除 icons/。

---

## 七、最小可用检查清单

```bash
# 0. 改了 assets/logo.svg 之后,先刷 PNG 源
python -c "import resvg_py; from pathlib import Path; Path('assets/logo-icon.png').write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=Path('assets/logo.svg').read_text(encoding='utf-8'), width=1024, height=1024)))"

# 1. 生成 Tauri 全套
(cd packages/desktop && bunx @tauri-apps/cli icon ../../assets/logo-icon.png --output tauri/icons)

# 2. 生成 Web favicon
python -c "
import resvg_py, shutil
from io import BytesIO
from pathlib import Path
from PIL import Image
svg = Path('assets/logo.svg').read_text(encoding='utf-8')
out = Path('packages/app/public'); out.mkdir(parents=True, exist_ok=True)
r = lambda n: Image.open(BytesIO(bytes(resvg_py.svg_to_bytes(svg_string=svg, width=n, height=n))))
sz = [(16,16),(32,32),(48,48)]
im = [r(s[0]) for s in sz]
im[0].save(out/'favicon.ico', format='ICO', sizes=sz, append_images=im[1:])
r(180).save(out/'apple-touch-icon.png')
r(16).save(out/'favicon-16.png'); r(32).save(out/'favicon-32.png')
shutil.copy(Path('assets/logo.svg'), out/'favicon.svg')
"

# 3. 确认 Tauri 至少这 5 个存在
ls packages/desktop/tauri/icons/{32x32,128x128,128x128@2x}.png \
   packages/desktop/tauri/icons/icon.{ico,icns}

# 4. 确认 Web favicon 5 个
ls packages/app/public/{favicon.svg,favicon.ico,apple-touch-icon.png,favicon-16.png,favicon-32.png}

# 5. tauri.conf.json 的 bundle.icon 含桌面 5 条;index.html 含 3 条 link rel=icon

# 6. cargo check 过
(cd packages/desktop/tauri && cargo check)
```

六条都对,打包 / dev / 浏览器 tab 都能正常显示 logo。

---

## 参考

- Tauri 2 icon 命令:<https://v2.tauri.app/reference/cli/#icon>
- 推荐源尺寸 / 设计规范:<https://v2.tauri.app/distribute/icons/>
