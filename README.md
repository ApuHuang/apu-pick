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
   - MAD 最小算 2 分：整批品質很一致時，不會因為差零點幾分就被 reject

不想用自動門檻的話，可以直接指定（兩者擇一，都是每組各自算）：

- `--min-score 85`：分數低於 85 就 reject
- `--keep-best 0.7`：只留分數最高的前 70%（以能評分的張數計算；量不出來的一律 reject，不算在內）

### 分組

預設依**濾鏡 + 曝光時間**分組，每組各自算範本和門檻。L 跟 Ha、60s 跟 300s 的背景和星點數
差好幾倍，混在一起算會把整組窄頻或短曝光的全部 reject。

多晚的資料可以加上 `night`（`--group-by filter,exposure,night`），每晚各自比較。
「一晚」是依 DATE-OBS 相鄰兩張間隔超過 4 小時切開的，跨過午夜也算同一晚。

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

# 直接指定門檻：分數 85 以下 reject，或只留最好的 70%
python -m astro_light_selector D:\astro\M31\lights --min-score 85 --dry-run
python -m astro_light_selector D:\astro\M31\lights --keep-best 0.7 --dry-run

# 多晚資料每晚分開比較，並輸出趨勢圖 <folder>\selection_plot.png
python -m astro_light_selector D:\astro\M31\lights --group-by filter,exposure,night --plot --dry-run

# 後悔了：把 rejected 裡的檔案全部搬回原位
python -m astro_light_selector D:\astro\M31\lights --restore
```

趨勢圖需要 matplotlib（`pip install matplotlib`）。圖上依分組、拍攝順序排列各指標，
keep 藍點、reject 紅叉，分數格的虛線是每組的 keep 門檻；超出範圍的極端值（天亮、厚雲）
會用三角形貼在邊上。

### 搬檔與重跑

- 只搬 reject 的，keep 的不動；同名檔案不覆蓋，自動改名 `_1`、`_2`…
- 每次搬移都記在 reject 資料夾的 `moves.csv`（原位置 → 目前位置）
- **重跑時檔案位置會跟著新結果走**：門檻放寬後變成 keep 的，會自動從 rejected 搬回原位；
  收緊後新的 reject 照常搬出去。`--from-report` 或重新量測都一樣
- 重新量測時，之前被程式搬去 rejected 的也會一起量、算進同一批。門檻是相對整批算的，
  只看留下來的會越挑越嚴
- 自己手動丟進 rejected 的檔案（`moves.csv` 裡沒有的）程式不會動，也不會算進來
- 重跑時 `--reject-dir` 要跟上次一樣，程式才找得到紀錄
- `--restore` 把 rejected 裡的全部搬回原位（包含手動丟進去的）

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
| `--group-by` | `filter,exposure` | 分組欄位（`filter` / `exposure` / `night`），`none` 表示整批一起算 |
| `--plot [PNG]` | | 輸出趨勢圖（預設 `<folder>/selection_plot.png`） |
| `--restore` | | 把 reject 資料夾的檔案搬回原位後結束 |
| **評分模式** | | |
| `--top-frac` | 0.10 | 每個指標取最好的前幾成當範本 |
| `--pass-pct` | 0.80 | 及格線 = 範本分數 × 此值 |
| `--margin-k` | 1.5 | 眾數往下容許幾個 MAD |
| `--weights` | | 例如 `fwhm=0.35,eccentricity=0.3,n_stars=0.2,background=0.15` |
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
│   ├── grouping.py    # 依濾鏡 / 曝光 / 夜晚分組
│   ├── selector.py    # keep / reject 判斷
│   ├── report.py      # CSV 報表與摘要輸出
│   ├── mover.py       # 搬移 / 還原 reject 檔案
│   ├── plot.py        # 趨勢圖
│   └── cli.py         # 命令列介面
├── tests/
├── samples/           # 測試用 FITS（不進 git）
└── requirements.txt
```
