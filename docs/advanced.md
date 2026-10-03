# 進階說明

APU Pick 的進階說明：給想用命令列、調更多參數，或自己改程式、打包的人。一般使用請看 [README](../README.md)。

## 量測的指標

| 指標 | 說明 |
|---|---|
| `n_stars` | 偵測到的星點數量（雲、霧、對焦跑掉時會明顯下降） |
| `fwhm` | 對最亮 100 顆星做橢圓 2D Gaussian 擬合的 FWHM 中位數（像素） |
| `eccentricity` | 星點離心率中位數，0 = 正圓，越大越拖線 |
| `background` | 背景亮度中位數（ADU），雲 / 月光 / 光害會拉高 |
| `noise` | 背景雜訊標準差 |
| `snr` | 量 FWHM 用的那些亮星：峰值 / 背景雜訊 的中位數（薄雲、天亮會明顯下降） |
| `saturated_frac` | 飽和像素比例 |
| `altitude` | 目標仰角（度），曝光中間點 |
| `moon_alt` / `moon_sep` / `moon_illum` | 月亮仰角、月亮離目標的角距離（度）、月相（被照亮的比例 0~1） |
| `iso` / `gain` | 單眼 RAW 的 ISO、天文相機 header 的 `GAIN` |

OSC（Bayer）影像會先做 2x2 super-pixel 再偵測，Fuji X-Trans 做 3x3，FWHM 會換算回原始像素尺度。
單眼 RAW 用 rawpy（LibRaw）讀原始 CFA 資料並扣掉黑位，快門、ISO、拍攝時間從 EXIF 讀。

熱像素不算星點：3x3 範圍內中心那格佔了 70% 以上亮度的偵測結果視為熱像素，不算進星點數、也不拿來量星形。

仰角與月亮要 header 有目標座標（`RA`/`DEC`、`OBJCTRA`/`OBJCTDEC` 或 WCS 的 `CRVAL1`/`CRVAL2`）、
地點（`SITELAT`/`SITELONG`）和 UTC 的 `DATE-OBS`，用 astropy 內建星曆計算，不需要連網；缺任何一項就留空。
這幾個只是參考資訊，不影響評分。

## 挑片邏輯

預設是**評分模式**（`--mode score`）：

1. 讀不到、偵測不到星、能擬合的星 < 10 顆（被雲遮住）→ 直接淘汰
2. 每個指標各取整批最好的前 10% 平均，組成「最佳範本」
3. 每張影像每個指標換算成相對範本的比例（範本 = 1，上限 1），加權後 × 100 = 分數
   - 建議權重：FWHM 35%、離心率 30%、星點數 20%、背景 15%、SNR 0%（`--weights` 或視窗的「評分權重」可改）
4. 用 KDE 找分數分布的峰值（眾數）
5. 保留門檻 = max(眾數 − 1.5 × MAD, 100 × 80%)
   - 平常由眾數決定；整批偏差、眾數掉到及格線以下時，由及格線頂住
   - MAD 最小算 2 分：整批品質很一致時，不會因為差零點幾分就被淘汰

不想用自動門檻的話，可以直接指定（兩者擇一，都是每組各自算）：

- `--min-score 85`：分數低於 85 就淘汰
- `--keep-best 0.7`：只留分數最高的前 70%（以能評分的張數計算；量不出來的一律淘汰，不算在內）

### 分組

預設依**濾鏡 + 曝光時間 + ISO／增益**分組，每組各自算範本和門檻。L 跟 Ha、60s 跟 300s、
ISO 800 跟 1600 的背景、雜訊和星點數差好幾倍，混在一起算會把整組窄頻、短曝光或低 ISO 的全部淘汰。
ISO／增益（`gain`）：單眼看 RAW 的 ISO，天文相機看 header 的 `GAIN`；整批都沒有這項資料時不分。

多晚的資料可以加上 `night`（`--group-by filter,exposure,gain,night`），每晚各自比較。
「一晚」是依 DATE-OBS 相鄰兩張間隔超過 8 小時切開的，不看日期，跨過午夜、中間被雲擋幾小時都還算同一晚。
自己用資料夾分好每晚的（見下面「子資料夾」），用 `folder` 依子資料夾分開更準。

單眼 B 快門的曝光時間常差一兩秒（301、302 秒），`--exposure-tolerance`（預設 2 秒）範圍內算同一組。

### 子資料夾

開啟的資料夾本身沒有影像、子資料夾裡有時（例如單眼的 `light/iso800/date_2026 0101/`、`date_2026 0202/`…），
會自動改成**包含子資料夾**，一次處理全部；也可以用 `--recursive`（視窗版是「量測」裡的開關）自己打開。

- 不限層數；`rejected` 以及名稱含 dark、flat、bias、master、calibrated 的資料夾會跳過（不分大小寫）
- 淘汰片預設搬到**每張自己所在資料夾的 `rejected/`**，各自有 `moves.csv`；
  指定 `--reject-dir`（視窗版是「淘汰片」的「變更…」）時，照原本的子資料夾結構放進去
  （`<reject-dir>/iso800/date_2026 0101/DSC00001.ARW`），每個資料夾各有一份 `moves.csv`。
  這個資料夾放在開啟的資料夾裡面也可以，掃描時會跳過它
- 預設整批一起評分（跟全部放在同一個資料夾的結果一樣）；`--group-by` 加上 `folder` 就每個子資料夾各自比較
- 報表只存在最上層一份，`file` 欄是相對路徑（例如 `iso800/date_2026 0101/DSC00001.ARW`），
  每晚檔名重複也分得開；之後單獨開某一晚的資料夾要重新量測
- 上次是用子資料夾模式存的報表、而且那些子資料夾還在，下次開同一個資料夾會自動包含子資料夾
- 報表的路徑對不上現在的檔案（檔案被搬過）時，用檔名認回量測結果；只認檔名唯一的，
  同名的有好幾個（單眼每晚重複）就當成新檔案。手動覆寫跟著換到新位置，存報表時改寫成新的路徑

### 整理檔案（視窗版）

右側「整理檔案」依勾選的參數把影像搬進子資料夾：相機（FITS 的 `INSTRUME`；RAW 讀相機寫的型號：一般從 EXIF，Fujifilm RAF 從檔頭，Canon CR3 從 CMT1）、濾鏡（`FILTER`）、
曝光時間（照「曝光誤差容許」合併，例如 `301~302s`）、ISO／增益（`ISO800`、`Gain111`）、每晚（第一張的日期）。
讀不到的放在 `UnknownCamera`、`NoFilter` 這類資料夾。資料夾名稱不跟著介面語言。

- 原本就在子資料夾裡的會保留那一層（`ASI2600MC/Ha/300s/date_0101/…`），同名檔案不會撞在一起
- 搬了哪些記在開啟的資料夾裡的 `organize_log.csv`（相對路徑），「復原整理」照它搬回原位
- 有淘汰片在淘汰片資料夾裡時要先「全部還原」，不然 `moves.csv` 的原位置會對不上

也可以用 `--mode rules`（只有命令列版）：四個指標各自獨立門檻（中位數 ± k × MAD 加上絕對上下限），
任何一項不合格就淘汰。

## 從原始碼執行

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .[gui]
```

視窗版：`python -m astro_light_selector.gui`，或 `apu-pick-gui`。

## 命令列版

命令列版的訊息只有繁體中文。

```bash
# 先用 dry-run 看結果，不搬檔案
python -m astro_light_selector D:\astro\M31\lights --dry-run

# 正式執行：淘汰的搬到 <folder>\rejected，報表存 <folder>\selection_report.csv
python -m astro_light_selector D:\astro\M31\lights

# 多核心平行處理、自訂門檻
python -m astro_light_selector D:\astro\M31\lights --workers 4 -k 2 --max-fwhm 5 --min-stars 50

# 調門檻不用重新量測：讀上次的報表重套
python -m astro_light_selector D:\astro\M31\lights --from-report D:\astro\M31\lights\selection_report.csv -k 1.2 --dry-run

# 直接指定門檻：分數 85 以下淘汰，或只留最好的 70%
python -m astro_light_selector D:\astro\M31\lights --min-score 85 --dry-run
python -m astro_light_selector D:\astro\M31\lights --keep-best 0.7 --dry-run

# 多晚資料每晚分開比較，並輸出趨勢圖 <folder>\selection_plot.png
python -m astro_light_selector D:\astro\M31\lights --group-by filter,exposure,night --plot --dry-run

# 後悔了：把 rejected 裡的檔案全部搬回原位
python -m astro_light_selector D:\astro\M31\lights --restore

# 單眼多晚資料：一次處理所有子資料夾，每晚的淘汰片搬到各自的 rejected
python -m astro_light_selector D:\astro\NGC7000\light --recursive

# 同上，但淘汰片集中到另一個資料夾，照子資料夾結構放
python -m astro_light_selector D:\astro\NGC7000\light --recursive --reject-dir D:\astro\NGC7000\second-pass
```

`pip install -e .` 之後也可以直接用 `apu-pick <folder>`。

趨勢圖需要 matplotlib（`pip install matplotlib`）。圖上依分組、拍攝順序排列各指標，
保留是藍點、淘汰是紅叉，分數格的虛線是每組的保留門檻；超出範圍的極端值（天亮、厚雲）
會用三角形貼在邊上。

報表 `selection_report.csv` 的欄位名稱和 `keep` 欄的值（keep / reject）是給程式讀的資料格式，
維持英文；`reasons` 欄的淘汰原因會用產生報表時的介面語言。

`override` 欄是視窗版「強制保留／強制淘汰」的紀錄（keep / reject，空白 = 自動）。
命令列重跑時也會照這一欄，不會被新的門檻蓋掉；想取消就把那格清空。

### 搬檔與重跑

- 只搬淘汰的，保留的不動；同名檔案不覆蓋，自動改名 `_1`、`_2`…
- 每次搬移都記在淘汰片資料夾的 `moves.csv`（原位置 → 目前位置）
- **重跑時檔案位置會跟著新結果走**：門檻放寬後變成保留的，會自動從 rejected 搬回原位；
  收緊後新的淘汰片照常搬出去。`--from-report` 或重新量測都一樣
- 重新量測時，之前被程式搬去 rejected 的也會一起量、算進同一批。門檻是相對整批算的，
  只看留下來的會越挑越嚴
- 自己手動丟進 rejected 的檔案（`moves.csv` 裡沒有的）程式不會動，也不會算進來
- 重跑時 `--reject-dir` 要跟上次一樣，程式才找得到紀錄
- `--restore` 把 rejected 裡的全部搬回原位（包含手動丟進去的）

### 完整參數

`python -m astro_light_selector --help`

| 參數 | 預設 | 說明 |
|---|---|---|
| `--reject-dir` | `<folder>/rejected` | 淘汰片搬去的資料夾（子資料夾模式預設是各資料夾裡的 `rejected`，指定時照子資料夾結構放） |
| `--recursive` | 自動 | 包含子資料夾；folder 本身沒有影像、子資料夾有時自動打開 |
| `--report` | `<folder>/selection_report.csv` | CSV 報表路徑 |
| `--dry-run` | | 只分析、輸出報表，不搬檔案 |
| `--workers N` | 1 | 平行處理數 |
| `--from-report CSV` | | 不重新量測，讀舊報表重套門檻 |
| `--mode` | score | `score` 評分模式 / `rules` 規則模式 |
| `--group-by` | `filter,exposure,gain` | 分組欄位（`filter` / `exposure` / `gain`（ISO／增益）/ `night` / `folder`（子資料夾）），`none` 表示整批一起算 |
| `--exposure-tolerance` | 2 | 曝光時間差在幾秒以內算同一組 |
| `--plot [PNG]` | | 輸出趨勢圖（預設 `<folder>/selection_plot.png`） |
| `--restore` | | 把淘汰片資料夾的檔案搬回原位後結束 |
| **評分模式** | | |
| `--top-frac` | 0.10 | 每個指標取最好的前幾成當範本 |
| `--pass-pct` | 0.80 | 及格線 = 範本分數 × 此值 |
| `--margin-k` | 1.5 | 眾數往下容許幾個 MAD |
| `--weights` | | 例如 `fwhm=0.35,eccentricity=0.3,n_stars=0.2,background=0.15,snr=0` |
| `--min-score` | | 直接指定門檻（0~100），取代自動門檻 |
| `--keep-best` | | 只留分數最高的這個比例（0~1），取代自動門檻 |
| **規則模式** | | |
| `-k` | | 一次設定四個相對門檻的 k |
| `--fwhm-k` / `--ecc-k` | 1.5 | FWHM / 離心率 的 MAD 倍數 |
| `--stars-k` | 2.0 | 星點數 的 MAD 倍數 |
| `--background-k` | 3.0 | 背景 的 MAD 倍數 |
| `--max-fwhm` | 不用 | FWHM 絕對上限（像素） |
| `--min-stars` | 20 | 星點數絕對下限 |
| `--max-ecc` | 0.7 | 離心率絕對上限 |
| `--max-saturated` | 不用 | 飽和像素比例上限 |

絕對門檻設 `-1` 表示不使用。

## 自己打包

```bash
pip install -e .[exe]
python packaging/build_exe.py
```

- Windows：產出 `dist\APUPick\APUPick.exe` 和 `dist\APUPick-<版本>-win64.zip`
- Mac：產出 `dist/APUPick.app` 和 `dist/APUPick-<版本>-macos-<arm64|x86_64>.zip`

打包完會自動用合成星場跑一次打包好的程式，確認量測（含多核心）與畫圖正常。

PyInstaller 不能跨平台打包，Mac 版要在 Mac 上打包：發布 Release 時 GitHub Actions 會用雲端 Mac
自動打包並附到 Release 上（`.github/workflows/build-macos.yml`），也可以在 Actions 頁面手動執行。

圖示和 Logo 由 `packaging/make_assets.py` 產生。介面文字（繁體中文 / English）都在
`src/astro_light_selector/i18n.py`，介面語言、面板收合、權重等設定存在系統的應用程式設定資料夾
（Windows：`%APPDATA%\APU Pick\settings.json`）。

## 測試

```bash
.venv\Scripts\python -m pytest
```

`tests/synth.py` 可產生合成星場來試跑：

```bash
.venv\Scripts\python tests/synth.py samples
```

## 專案結構

```
apu-pick/
├── src/astro_light_selector/
│   ├── metrics.py     # 單張影像的品質量測
│   ├── rawfile.py     # 單眼 RAW 讀取
│   ├── sky.py         # 目標仰角、月亮（從 header 算）
│   ├── preview.py     # 預覽縮圖（自動拉伸）
│   ├── scoring.py     # 最佳範本評分
│   ├── grouping.py    # 依濾鏡 / 曝光 / ISO／增益 / 夜晚 / 子資料夾分組
│   ├── selector.py    # 保留 / 淘汰判斷
│   ├── report.py      # CSV 報表與摘要輸出
│   ├── mover.py       # 搬移 / 還原淘汰片
│   ├── organize.py    # 依參數整理進子資料夾 / 復原
│   ├── plot.py        # 趨勢圖
│   ├── pipeline.py    # 量測 → 分組 → 挑片流程（命令列、視窗共用）
│   ├── cli.py         # 命令列介面
│   ├── gui.py         # 視窗介面
│   ├── i18n.py        # 介面文字（繁體中文 / English）
│   ├── settings.py    # 使用者設定（介面語言、權重…）
│   └── assets/        # 程式圖示
├── packaging/         # 打包：build_exe.py、進入點、圖示與 Logo 產生
├── docs/              # 進階說明、截圖、Logo
├── .github/workflows/ # 雲端打包 Mac 版
├── tests/
└── requirements.txt
```
