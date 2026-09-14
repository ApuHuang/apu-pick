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

預設是**評分模式**（`--mode score`）：

1. 讀不到、偵測不到星、能擬合的星 < 10 顆（被雲遮住）→ 直接 reject
2. 每個指標各取整批最好的前 10% 平均，組成「最佳範本」
3. 每張影像每個指標換算成相對範本的比例（範本 = 1，上限 1），加權後 × 100 = 分數
   - FWHM 35%、離心率 30%、星點數 20%、背景 15%
4. 用 KDE 找分數分布的峰值（眾數）
5. keep 門檻 = max(眾數 − 1.5 × MAD, 100 × 80%)
   - 平常由眾數決定；整批偏差、眾數掉到及格線以下時，由及格線頂住

也可以用 `--mode rules`：四個指標各自獨立門檻（中位數 ± k × MAD 加上絕對上下限），
任何一項不合格就 reject。

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
python -m astro_light_selector D:\astro\M31\lights --workers 4 -k 2 --max-fwhm 5 --min-stars 50

# 調門檻不用重新量測：讀上次的報表重套
python -m astro_light_selector D:\astro\M31\lights --from-report D:\astro\M31\lights\selection_report.csv -k 1.2 --dry-run
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
| `--mode` | score | `score` 評分模式 / `rules` 規則模式 |
| **評分模式** | | |
| `--top-frac` | 0.10 | 每個指標取最好的前幾成當範本 |
| `--pass-pct` | 0.80 | 及格線 = 範本分數 × 此值 |
| `--margin-k` | 1.5 | 眾數往下容許幾個 MAD |
| `--weights` | | 例如 `fwhm=0.35,eccentricity=0.3,n_stars=0.2,background=0.15` |
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
│   ├── scoring.py     # 最佳範本評分
│   ├── selector.py    # keep / reject 判斷
│   ├── report.py      # CSV 報表與摘要輸出
│   ├── mover.py       # 搬移 reject 檔案
│   └── cli.py         # 命令列介面
├── tests/
├── samples/           # 測試用 FITS（不進 git）
└── requirements.txt
```
