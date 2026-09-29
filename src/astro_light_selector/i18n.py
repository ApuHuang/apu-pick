"""介面文字：繁體中文（預設）與英文，用詞比照 APU Astro。

程式裡只放代號，顯示時才用 tr() 依目前語言轉成文字。量測錯誤存成「代號:細節」
（例如 few_fit_stars:5），報表和介面顯示時再用 tr_error() 轉成當下語言的說明。
"""

from __future__ import annotations

import re

APP_NAME = "APU Pick"
APP_SUBTITLE = "Astrophotography Pick Utility"

LANGUAGES = {"zh": "繁體中文", "en": "English"}
DEFAULT_LANGUAGE = "zh"
_language = DEFAULT_LANGUAGE


def set_language(lang: str) -> None:
    global _language
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language: {lang}")
    _language = lang


def get_language() -> str:
    return _language


def tr(key: str, **kw: object) -> str:
    text = _CATALOG[_language].get(key)
    if text is None:
        text = _CATALOG[DEFAULT_LANGUAGE][key]
    return text.format(**kw) if kw else text


def tr_metric(metric: str) -> str:
    return tr(f"metric.{metric}")


def tr_error(error: str) -> str:
    """把「代號:細節」轉成目前語言的說明；認不得的（舊版報表的文字）原樣顯示。"""
    code, _, detail = error.partition(":")
    key = f"error.{code}"
    if key not in _CATALOG[DEFAULT_LANGUAGE]:
        return error
    return tr(key, detail=detail)


# 舊版（0.2 以前）報表裡直接存中文錯誤訊息，讀進來時轉回代號
_LEGACY_ERRORS = [
    (re.compile(r"^讀取失敗: ?(.*)$", re.S), "read_failed:{0}"),
    (re.compile(r"^背景雜訊為 0"), "blank"),
    (re.compile(r"^偵測不到星點"), "no_stars"),
    (re.compile(r"^只有 (\d+) 顆星能擬合"), "few_fit_stars:{0}"),
]


def normalize_error(error: str) -> str:
    for pattern, code in _LEGACY_ERRORS:
        m = pattern.match(error)
        if m:
            return code.format(*m.groups())
    return error


_ZH: dict[str, str] = {
    "term.keep": "保留",
    "term.reject": "淘汰",

    "metric.fwhm": "FWHM",
    "metric.eccentricity": "離心率",
    "metric.n_stars": "星點數",
    "metric.background": "背景",
    "metric.snr": "SNR",

    "error.read_failed": "讀取失敗：{detail}",
    "error.blank": "背景雜訊為 0，可能是空白影像",
    "error.no_stars": "偵測不到星點",
    "error.few_fit_stars": "只有 {detail} 顆星能擬合，可能被雲遮住或只剩熱像素",

    "reason.unscorable": "無法評分",
    "reason.manual_keep": "手動保留",
    "reason.manual_reject": "手動淘汰",
    "reason.score_low": "分數 {score:.1f} < {threshold:.1f}（弱項：{metric} {ratio:.2f}）",
    "reason.fwhm_high": "FWHM {value:.2f} > {limit:.2f}",
    "reason.fwhm_max": "FWHM {value:.2f} > 上限 {limit}",
    "reason.stars_low": "星點數 {value} < {limit:.0f}",
    "reason.stars_min": "星點數 {value} < 下限 {limit}",
    "reason.ecc_high": "離心率 {value:.2f} > {limit:.2f}",
    "reason.ecc_max": "離心率 {value:.2f} > 上限 {limit}",
    "reason.bkg_high": "背景 {value:.0f} > {limit:.0f}",
    "reason.saturated_max": "飽和比例 {value:.4f} > 上限 {limit}",

    "group.filter": "濾鏡 {value}",
    "group.no_filter": "無濾鏡",
    "group.exposure": "曝光 {value}",
    "group.night": "{value} 晚",
    "group.sep": "／",
    "group.all": "全部",

    "summary.reference": "範本（各指標最好的一段取平均）：{parts}",
    "summary.part_sep": "、",
    "summary.stats": "分數眾數 {mode:.1f}，MAD {mad:.1f}，及格線 {pass_line:.0f}",
    "summary.threshold": "保留門檻＝{threshold:.1f}（{how}）",
    "summary.how.mode": "由眾數決定",
    "summary.how.pass_line": "由及格線決定",
    "summary.how.min_score": "指定最低分數",
    "summary.how.keep_best": "只留最好的 {n} 張",
    "summary.no_measurable": "這組沒有任何一張能量測，全部淘汰",
    "summary.group_header": "【{label}】{n} 張",
    "summary.total": "共 {n} 張，保留 {keep}，淘汰 {reject}",
    "summary.reject_list": "淘汰清單（分數低到高）：",

    "plot.score": "分數",
    "plot.fwhm": "FWHM (px)",
    "plot.eccentricity": "離心率",
    "plot.n_stars": "星點數",
    "plot.background": "背景 (ADU)",
    "plot.snr": "SNR",
    "plot.altitude": "仰角 (°)",
    "plot.target": "目標",
    "plot.moon": "月亮",
    "plot.moon_phase": "月相 {p:.0f}%",
    "plot.title": "共 {n} 張：保留 {keep}，淘汰 {reject}",
    "plot.xlabel": "拍攝順序",
    "plot.placeholder": "量測完成後，這裡會顯示各指標的趨勢圖",

    "gui.no_folder": "尚未開啟資料夾",
    "gui.btn.open": "開啟",
    "gui.btn.open.help": "開啟放 light frames 的資料夾（Ctrl+O）",
    "gui.btn.measure": "量測",
    "gui.btn.remeasure": "重新量測",
    "gui.btn.measure.help": "量測每張的 FWHM、離心率、星點數與背景；幾百張約需幾分鐘",
    "gui.btn.stop": "停止",
    "gui.btn.move": "搬移淘汰片",
    "gui.btn.move.help": "把淘汰的片搬到淘汰片資料夾；隨時可以全部還原",
    "gui.btn.load": "讀取上次結果",
    "gui.btn.change": "變更…",
    "gui.btn.restore": "全部還原…",
    "gui.details": "說明",
    "gui.welcome.hint": "按右上角「開啟」，選擇放 light frames 的資料夾",
    "gui.welcome.ready": "按右上角「量測」開始",
    "gui.welcome.measuring": "量測中，完成後會在這裡顯示結果",

    "gui.group.result": "結果",
    "gui.group.result.info": "目前門檻下的保留與淘汰張數。門檻或分組一改，這裡、趨勢圖和清單都會立刻更新。",
    "gui.metric.frames": "張數",
    "gui.metric.kept": "保留",
    "gui.metric.rejected": "淘汰",
    "gui.metric.threshold": "門檻",
    "gui.metric.per_group": "各組不同",
    "gui.metric.ref_fwhm": "範本 FWHM",
    "gui.group.threshold": "保留門檻",
    "gui.group.threshold.info": "每張 0~100 分，是跟這批最好的 10% 比出來的。\n"
                                "自動：取「分數眾數−1.5×MAD」和及格線兩者較高的，大部分情況用這個就好。\n"
                                "最低分數：分數低於這個值就淘汰。\n"
                                "只留最好：只保留分數最高的這個比例。",
    "gui.method.auto": "自動",
    "gui.method.min_score": "最低分數",
    "gui.method.keep_best": "只留最好",
    "gui.slider.pass_line": "及格線",
    "gui.slider.min_score": "最低分數",
    "gui.slider.keep_best": "保留比例",
    "gui.value.points": "{v:.0f} 分",
    "gui.group.weights": "評分權重",
    "gui.group.weights.info": "每個指標在總分裡佔多少。滑桿是相對比例，右邊會換算成百分比；設成 0 就不看這一項。\n"
                              "建議權重（FWHM 35%、離心率 30%、星點數 20%、背景 15%）是對照肉眼挑片調出來的。\n"
                              "SNR 是亮星的訊噪比，薄雲、背景變亮都會讓它下降；預設 0（不計分），需要時再打開。\n"
                              "改了權重，分數分布和自動門檻都會跟著變。",
    "gui.weights.recommended": "建議權重",
    "gui.weights.custom": "自訂權重",
    "gui.btn.reset_weights": "恢復建議值",
    "gui.status.no_weights": "權重不能全部是 0",
    "summary.weights": "權重：{parts}",
    "gui.group.grouping": "分組",
    "gui.group.grouping.info": "每組各自算範本和門檻。不同濾鏡、曝光時間的背景和星點數差很多，混在一起算，"
                               "整組窄頻或短曝光的片會全部被淘汰。多晚的資料可以開「每晚分開」，每晚各自比較。"
                               "單眼的 B 快門計時常有一兩秒誤差（例如 301、302 秒），曝光差在「曝光誤差容許」以內的算同一組。",
    "gui.toggle.filter": "依濾鏡分開",
    "gui.toggle.exposure": "依曝光時間分開",
    "gui.toggle.night": "每晚分開",
    "gui.slider.exposure_tolerance": "曝光誤差容許",
    "gui.value.seconds": "{v:.0f} 秒",
    "gui.group.measure": "量測",
    "gui.group.measure.info": "平行處理數越大越快，但也越吃記憶體，電腦變卡就調低。"
                              "量完的結果會存成資料夾裡的 selection_report.csv，下次開同一個資料夾會自動讀取。\n\n"
                              "監看資料夾：拍攝時打開，每 10 秒檢查一次，新檔案寫完就自動量測、加進結果，"
                              "門檻跟著整批重算。只量測不搬檔，搬檔還是要自己按。",
    "gui.slider.workers": "平行處理數",
    "gui.toggle.watch": "監看資料夾",
    "gui.status.watching": "監看中：共 {n} 張，有新檔案會自動量測",
    "gui.status.watch_measuring": "監看中：量測 {n} 張新檔案…",
    "gui.status.watch_added": "監看中：新增 {n} 張，最新是 {name}，共 {total} 張",
    "gui.status.watch_off": "已停止監看資料夾",
    "gui.group.rejects": "淘汰片",
    "gui.group.rejects.info": "淘汰的片只會搬到這個資料夾，不會刪除；按「全部還原」就會搬回原位。\n"
                              "用 PixInsight WBPP 的「+ Directory」加入整個 light 資料夾時，子資料夾也會被加進去，"
                              "可以把這裡改到 light 資料夾外面。",
    "gui.reject_folder": "淘汰片資料夾",
    "gui.tab.plot": "趨勢圖",
    "gui.tab.list": "清單",
    "gui.tab.details": "門檻細節",
    "gui.only_reject": "只看淘汰片",
    "gui.btn.force_keep": "強制保留",
    "gui.btn.force_reject": "強制淘汰",
    "gui.btn.clear_override": "改回自動",
    "gui.override.help": "選取清單裡的片（Ctrl / Shift 可多選）再按這裡，或在清單上按右鍵；手動決定會一直保留，重新量測或調門檻都不會改掉",
    "gui.metric.manual": "手動覆寫",
    "gui.preview.hint": "點清單裡的一張片，這裡會顯示預覽",
    "gui.preview.loading": "載入中…",
    "gui.preview.failed": "無法預覽：{error}",
    "gui.preview.missing": "找不到這個檔案",
    "gui.preview.closeup": "放大 ×2（點縮圖換位置）",
    "gui.preview.sky_moon_up": "目標仰角 {alt:.0f}°｜月亮仰角 {moon:.0f}°，照亮 {illum:.0f}%，離目標 {sep:.0f}°",
    "gui.preview.sky_moon_down": "目標仰角 {alt:.0f}°｜月亮在地平線下（照亮 {illum:.0f}%）",
    "gui.col.file": "檔名",
    "gui.col.result": "結果",
    "gui.col.score": "分數",
    "gui.col.fwhm": "FWHM",
    "gui.col.ecc": "離心率",
    "gui.col.stars": "星點數",
    "gui.col.bkg": "背景",
    "gui.col.snr": "SNR",
    "gui.col.alt": "仰角",
    "gui.col.moon": "月亮仰角",
    "gui.col.group": "分組",
    "gui.col.reason": "原因",
    "gui.pick_folder": "選擇放 light frames 的資料夾",
    "gui.pick_reject": "選擇淘汰片要搬去的資料夾",
    "gui.status.start": "按「開啟」選擇放 light frames 的資料夾",
    "gui.status.no_folder": "找不到資料夾：{folder}",
    "gui.status.found": "資料夾裡有 {n} 張 FITS，按「量測」開始",
    "gui.status.no_fits": "這個資料夾裡沒有 FITS 檔案",
    "gui.status.loaded": "已讀取上次的量測結果（{n} 張），調門檻會即時更新",
    "gui.status.loaded_new": "。注意：有 {n} 張新檔案不在上次結果裡，建議按「重新量測」",
    "gui.err.load": "讀取上次結果失敗：\n{error}",
    "gui.warn.pick_folder": "請先開啟放 light frames 的資料夾",
    "gui.status.measuring": "量測中 0/{n}{extra}…",
    "gui.status.includes_rejected": "（含之前淘汰的 {n} 張）",
    "gui.status.stopping": "停止中，等正在量的幾張做完…",
    "gui.status.progress": "量測中 {i}/{n}{left}：{name}",
    "gui.status.left_min": "，約剩 {m:.0f} 分",
    "gui.status.left_sec": "，約剩 {s:.0f} 秒",
    "gui.warn.report_save": "報表存不進去：{error}",
    "gui.status.done": "量測完成：{n} 張，花了 {m:.1f} 分",
    "gui.status.done_errors": "，其中 {n} 張量不到星",
    "gui.status.done_saved": "。結果已存到 {report}，下次開這個資料夾會直接讀取",
    "gui.status.cancelled": "已停止量測",
    "gui.status.failed": "量測失敗",
    "gui.err.measure": "量測時發生錯誤：\n\n{error}",
    "gui.status.bad_threshold": "門檻數字不合理：分數要在 0~100，只留最好的要在 1~100%",
    "gui.summary": "共 {n} 張：保留 {keep}，淘汰 {reject}{extra}",
    "gui.summary.one_threshold": "，門檻 {t:.1f} 分",
    "gui.summary.groups": "，{g} 組各自算門檻",
    "gui.move.nothing": "檔案位置已經跟目前的結果一致，不用搬。",
    "gui.move.out": "把 {n} 張淘汰片搬到：\n{dir}",
    "gui.move.back": "把 {n} 張之前淘汰、現在變成保留的搬回原位",
    "gui.move.confirm": "確定要搬嗎？之後可以用「全部還原」搬回來。",
    "gui.move.error": "搬移時發生錯誤：\n{error}\n\n已經搬好的有記錄，可以用「全部還原」搬回。",
    "gui.move.done_out": "已搬移 {n} 張淘汰片",
    "gui.move.done_back": "已搬回 {n} 張",
    "gui.move.sep": "，",
    "gui.move.where": "（{dir}）",
    "gui.restore.nothing": "淘汰片資料夾裡沒有可以搬回的檔案。",
    "gui.restore.confirm": "把 {dir} 裡的 {n} 張 FITS 全部搬回原位？\n（手動放進去的也會一起搬回 light 資料夾）",
    "gui.restore.error": "搬回時發生錯誤：\n{error}",
    "gui.restore.done": "已搬回 {n} 張",
    "gui.close.confirm": "還在量測中，確定要關閉嗎？",
}

_EN: dict[str, str] = {
    "term.keep": "Keep",
    "term.reject": "Reject",

    "metric.fwhm": "FWHM",
    "metric.eccentricity": "eccentricity",
    "metric.n_stars": "star count",
    "metric.background": "background",
    "metric.snr": "SNR",

    "error.read_failed": "Could not read file: {detail}",
    "error.blank": "Background noise is zero, possibly a blank image",
    "error.no_stars": "No stars detected",
    "error.few_fit_stars": "Only {detail} stars could be fitted, probably clouds or only hot pixels",

    "reason.unscorable": "Could not be scored",
    "reason.manual_keep": "Kept manually",
    "reason.manual_reject": "Rejected manually",
    "reason.score_low": "Score {score:.1f} < {threshold:.1f} (weakest: {metric} {ratio:.2f})",
    "reason.fwhm_high": "FWHM {value:.2f} > {limit:.2f}",
    "reason.fwhm_max": "FWHM {value:.2f} > max {limit}",
    "reason.stars_low": "Stars {value} < {limit:.0f}",
    "reason.stars_min": "Stars {value} < min {limit}",
    "reason.ecc_high": "Eccentricity {value:.2f} > {limit:.2f}",
    "reason.ecc_max": "Eccentricity {value:.2f} > max {limit}",
    "reason.bkg_high": "Background {value:.0f} > {limit:.0f}",
    "reason.saturated_max": "Saturated fraction {value:.4f} > max {limit}",

    "group.filter": "Filter {value}",
    "group.no_filter": "No filter",
    "group.exposure": "{value} exposure",
    "group.night": "Night of {value}",
    "group.sep": " / ",
    "group.all": "All frames",

    "summary.reference": "Reference (average of the best frames per metric): {parts}",
    "summary.part_sep": ", ",
    "summary.stats": "Score mode {mode:.1f}, MAD {mad:.1f}, pass line {pass_line:.0f}",
    "summary.threshold": "Keep threshold = {threshold:.1f} ({how})",
    "summary.how.mode": "set by the score distribution",
    "summary.how.pass_line": "held at the pass line",
    "summary.how.min_score": "minimum score you set",
    "summary.how.keep_best": "keeping the best {n} frames",
    "summary.no_measurable": "No frame in this group could be measured; all rejected",
    "summary.group_header": "[{label}] {n} frames",
    "summary.total": "{n} frames: {keep} kept, {reject} rejected",
    "summary.reject_list": "Rejected frames (lowest score first):",

    "plot.score": "Score",
    "plot.fwhm": "FWHM (px)",
    "plot.eccentricity": "Eccentricity",
    "plot.n_stars": "Stars",
    "plot.background": "Background (ADU)",
    "plot.snr": "SNR",
    "plot.altitude": "Altitude (°)",
    "plot.target": "Target",
    "plot.moon": "Moon",
    "plot.moon_phase": "Moon {p:.0f}%",
    "plot.title": "{n} frames: {keep} kept, {reject} rejected",
    "plot.xlabel": "Capture order",
    "plot.placeholder": "Trend charts appear here after measuring",

    "gui.no_folder": "No folder open",
    "gui.btn.open": "Open",
    "gui.btn.open.help": "Open a folder of light frames (Ctrl+O)",
    "gui.btn.measure": "Measure",
    "gui.btn.remeasure": "Measure Again",
    "gui.btn.measure.help": "Measure FWHM, eccentricity, star count and background of every frame; "
                            "a few hundred frames take a few minutes",
    "gui.btn.stop": "Stop",
    "gui.btn.move": "Move Rejects",
    "gui.btn.move.help": "Move rejected frames to the reject folder; you can restore them any time",
    "gui.btn.load": "Load Last Results",
    "gui.btn.change": "Change…",
    "gui.btn.restore": "Restore All…",
    "gui.details": "Details",
    "gui.welcome.hint": "Click “Open” at the top right to choose a folder of light frames",
    "gui.welcome.ready": "Click “Measure” at the top right to start",
    "gui.welcome.measuring": "Measuring… results appear here when done",

    "gui.group.result": "Result",
    "gui.group.result.info": "Kept and rejected counts at the current threshold. Changing the threshold or grouping "
                             "updates this, the trends and the frame list immediately.",
    "gui.metric.frames": "Frames",
    "gui.metric.kept": "Kept",
    "gui.metric.rejected": "Rejected",
    "gui.metric.threshold": "Threshold",
    "gui.metric.per_group": "Per group",
    "gui.metric.ref_fwhm": "Reference FWHM",
    "gui.group.threshold": "Keep Threshold",
    "gui.group.threshold.info": "Every frame gets 0–100 points, compared with the best 10% of this batch.\n"
                                "Auto: the higher of “score mode − 1.5 × MAD” and the pass line. Right for most nights.\n"
                                "Min Score: frames below this score are rejected.\n"
                                "Keep Best: keep only this share of the highest-scoring frames.",
    "gui.method.auto": "Auto",
    "gui.method.min_score": "Min Score",
    "gui.method.keep_best": "Keep Best",
    "gui.slider.pass_line": "Pass Line",
    "gui.slider.min_score": "Minimum Score",
    "gui.slider.keep_best": "Keep Ratio",
    "gui.value.points": "{v:.0f}",
    "gui.group.weights": "Score Weights",
    "gui.group.weights.info": "How much each metric counts toward the score. Sliders are relative; the right side shows "
                              "the resulting share. Set one to 0 to ignore it.\n"
                              "The recommended weights (FWHM 35%, eccentricity 30%, stars 20%, background 15%) were "
                              "tuned against hand-picked frames.\n"
                              "SNR is the signal-to-noise of the bright stars; thin cloud or a brighter sky lowers it. "
                              "It defaults to 0 (not scored); turn it up if you need it.\n"
                              "Changing weights also changes the score distribution and the auto threshold.",
    "gui.weights.recommended": "Recommended",
    "gui.weights.custom": "Custom",
    "gui.btn.reset_weights": "Restore Recommended",
    "gui.status.no_weights": "Weights can't all be 0",
    "summary.weights": "Weights: {parts}",
    "gui.group.grouping": "Grouping",
    "gui.group.grouping.info": "Each group gets its own reference and threshold. Different filters and exposures differ "
                               "a lot in background and star count; scored together, a whole narrowband or short-exposure "
                               "set would be rejected. For several nights, turn on By Night to compare each night separately. "
                               "Camera bulb timing is often off by a second or two (301 s vs 302 s); exposures within "
                               "Exposure Tolerance of each other count as one group.",
    "gui.toggle.filter": "By Filter",
    "gui.toggle.exposure": "By Exposure",
    "gui.toggle.night": "By Night",
    "gui.slider.exposure_tolerance": "Exposure Tolerance",
    "gui.value.seconds": "{v:.0f} s",
    "gui.group.measure": "Measurement",
    "gui.group.measure.info": "More parallel workers is faster but uses more memory; lower it if the computer slows down. "
                              "Results are saved as selection_report.csv in the folder and load automatically next time.\n\n"
                              "Watch Folder: turn it on while shooting. The folder is checked every 10 seconds; "
                              "new files are measured once they finish writing and added to the results, "
                              "and thresholds are recalculated for the whole set. It only measures; "
                              "moving files is still up to you.",
    "gui.slider.workers": "Parallel Workers",
    "gui.toggle.watch": "Watch Folder",
    "gui.status.watching": "Watching: {n} frames. New files will be measured automatically.",
    "gui.status.watch_measuring": "Watching: measuring {n} new files…",
    "gui.status.watch_added": "Watching: added {n}, latest {name}, {total} frames in total",
    "gui.status.watch_off": "Stopped watching the folder",
    "gui.group.rejects": "Rejected Frames",
    "gui.group.rejects.info": "Rejected frames are only moved to this folder, never deleted; Restore All puts them back.\n"
                              "PixInsight WBPP's “+ Directory” also adds subfolders of the light folder, "
                              "so you may want this folder outside the light folder.",
    "gui.reject_folder": "Reject folder",
    "gui.tab.plot": "Trends",
    "gui.tab.list": "Frames",
    "gui.tab.details": "Threshold Details",
    "gui.only_reject": "Rejects Only",
    "gui.btn.force_keep": "Force Keep",
    "gui.btn.force_reject": "Force Reject",
    "gui.btn.clear_override": "Back to Auto",
    "gui.override.help": "Select frames in the list (Ctrl / Shift for several), then click here or right-click the list. "
                         "Manual choices stick through re-measuring and threshold changes.",
    "gui.metric.manual": "Manual overrides",
    "gui.preview.hint": "Select a frame in the list to preview it",
    "gui.preview.loading": "Loading…",
    "gui.preview.failed": "Can't preview: {error}",
    "gui.preview.missing": "File not found",
    "gui.preview.closeup": "Close-up ×2 (click the thumbnail to move)",
    "gui.preview.sky_moon_up": "Target altitude {alt:.0f}° | Moon {moon:.0f}° up, {illum:.0f}% lit, {sep:.0f}° away",
    "gui.preview.sky_moon_down": "Target altitude {alt:.0f}° | Moon below horizon ({illum:.0f}% lit)",
    "gui.col.file": "File",
    "gui.col.result": "Result",
    "gui.col.score": "Score",
    "gui.col.fwhm": "FWHM",
    "gui.col.ecc": "Eccentricity",
    "gui.col.stars": "Stars",
    "gui.col.bkg": "Background",
    "gui.col.snr": "SNR",
    "gui.col.alt": "Altitude",
    "gui.col.moon": "Moon Alt",
    "gui.col.group": "Group",
    "gui.col.reason": "Reason",
    "gui.pick_folder": "Choose the folder with your light frames",
    "gui.pick_reject": "Choose where rejected frames go",
    "gui.status.start": "Click “Open” to choose a folder of light frames",
    "gui.status.no_folder": "Folder not found: {folder}",
    "gui.status.found": "Found {n} FITS files. Click “Measure” to start.",
    "gui.status.no_fits": "No FITS files in this folder",
    "gui.status.loaded": "Loaded last results ({n} frames). Threshold changes update instantly.",
    "gui.status.loaded_new": " Note: {n} new files aren't in the last results. Consider measuring again.",
    "gui.err.load": "Could not load the last results:\n{error}",
    "gui.warn.pick_folder": "Open a folder of light frames first",
    "gui.status.measuring": "Measuring 0/{n}{extra}…",
    "gui.status.includes_rejected": " (including {n} previously rejected)",
    "gui.status.stopping": "Stopping after the frames in progress…",
    "gui.status.progress": "Measuring {i}/{n}{left}: {name}",
    "gui.status.left_min": ", about {m:.0f} min left",
    "gui.status.left_sec": ", about {s:.0f} s left",
    "gui.warn.report_save": "Could not save the report: {error}",
    "gui.status.done": "Done: {n} frames in {m:.1f} min",
    "gui.status.done_errors": ", {n} with no measurable stars",
    "gui.status.done_saved": ". Results saved to {report} and will load automatically next time.",
    "gui.status.cancelled": "Measuring stopped",
    "gui.status.failed": "Measuring failed",
    "gui.err.measure": "An error occurred while measuring:\n\n{error}",
    "gui.status.bad_threshold": "Invalid threshold: scores must be 0–100 and “Keep Best” 1–100%",
    "gui.summary": "{n} frames: {keep} kept, {reject} rejected{extra}",
    "gui.summary.one_threshold": ", threshold {t:.1f}",
    "gui.summary.groups": ", separate thresholds for {g} groups",
    "gui.move.nothing": "Files already match the current results. Nothing to move.",
    "gui.move.out": "Move {n} rejected frames to:\n{dir}",
    "gui.move.back": "Move {n} previously rejected frames that are now kept back to their original folder",
    "gui.move.confirm": "Go ahead? You can undo this later with “Restore All”.",
    "gui.move.error": "An error occurred while moving files:\n{error}\n\n"
                      "Files already moved are logged and can be put back with “Restore All”.",
    "gui.move.done_out": "Moved {n} rejected frames",
    "gui.move.done_back": "moved {n} back",
    "gui.move.sep": ", ",
    "gui.move.where": " ({dir})",
    "gui.restore.nothing": "There is nothing to restore in the reject folder.",
    "gui.restore.confirm": "Move all {n} FITS files in {dir} back?\n"
                           "(Files you put there by hand also go back to the light folder.)",
    "gui.restore.error": "An error occurred while restoring:\n{error}",
    "gui.restore.done": "Restored {n} frames",
    "gui.close.confirm": "Measuring is still running. Close anyway?",
}

_CATALOG = {"zh": _ZH, "en": _EN}
