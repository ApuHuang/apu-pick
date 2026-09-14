# astro-light-selector

天文攝影 Light frame 自動挑片程式。

掃描資料夾中的 FITS light frames，對每張量測品質指標，依門檻自動分類為
keep / reject，輸出 CSV 報表，並把 reject 的檔案搬到另一個資料夾。

## 量測的指標

| 指標 | 說明 |
|---|---|
| `n_stars` | 偵測到的星點數量（雲、霧、對焦跑掉時會明顯下降） |
| `fwhm` | 對最亮 100 顆星做橢圓 2D Gaussian 擬合的 FWHM 中位數（像素） |
| `eccentricity` | 星點離心率中位數，0 = 正圓，越大越拖線 |
| `background` | 背景亮度中位數（ADU），雲 / 月光 / 光害會拉高 |
| `noise` | 背景雜訊標準差 |
| `snr` | 星點峰值 / 背景雜訊 |
| `saturated_frac` | 飽和像素比例 |

OSC（Bayer）影像會先做 2x2 super-pixel 再偵測，FWHM 會換算回原始像素尺度。

## 挑片邏輯

兩層門檻，任何一項不合格就 reject：

1. **相對門檻**：以整批影像的 中位數 ± k × MAD 為準，自動適應當晚的 seeing 與天況。
   FWHM、離心率、背景過高，或星點數過低 → reject。預設 k：FWHM 1.5、離心率 1.5、
   星點數 2、背景 3（用實拍資料對照肉眼挑片調出來的；`-k` 可一次改四個，越小越嚴）。
2. **絕對門檻**：硬性限制，例如星點數 < 20、離心率 > 0.7。

讀取失敗或偵測不到星點的影像也會 reject。

## 環境安裝

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 使用方式

```bash
# 先用 dry-run 看結果，不搬檔案
python -m astro_light_selector D:\astro\M31\lights --dry-run

# 正式執行：reject 的搬到 <folder>\rejected，報表存 <folder>\selection_report.csv
python -m astro_light_selector D:\astro\M31\lights

# 多核心平行處理、自訂門檻
python -m astro_light_selector D:\astro\M31\lights --workers 4 --fwhm-k 2.5 --max-fwhm 5 --min-stars 50
```

也可以 `pip install -e .` 之後直接用 `astro-select <folder>` 指令。

完整參數：`python -m astro_light_selector --help`

| 參數 | 預設 | 說明 |
|---|---|---|
| `--reject-dir` | `<folder>/rejected` | reject 檔案搬去的資料夾 |
| `--report` | `<folder>/selection_report.csv` | CSV 報表路徑 |
| `--dry-run` | | 只分析、輸出報表，不搬檔案 |
| `--workers N` | 1 | 平行處理數 |
| `--from-report CSV` | | 不重新量測，讀舊報表重套門檻 |
| `-k` | | 一次設定四個相對門檻的 k |
| `--fwhm-k` / `--ecc-k` | 1.5 | FWHM / 離心率 的 MAD 倍數 |
| `--stars-k` | 2.0 | 星點數 的 MAD 倍數 |
| `--background-k` | 3.0 | 背景 的 MAD 倍數 |
| `--max-fwhm` | 不用 | FWHM 絕對上限（像素） |
| `--min-stars` | 20 | 星點數絕對下限 |
| `--max-ecc` | 0.7 | 離心率絕對上限 |
| `--max-saturated` | 不用 | 飽和像素比例上限 |

絕對門檻設 `-1` 表示不使用。

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
astro-light-selector/
├── src/astro_light_selector/
│   ├── metrics.py     # 單張影像的品質量測
│   ├── selector.py    # keep / reject 判斷
│   ├── report.py      # CSV 報表與摘要輸出
│   ├── mover.py       # 搬移 reject 檔案
│   └── cli.py         # 命令列介面
├── tests/
├── samples/           # 測試用 FITS（不進 git）
└── requirements.txt
```
