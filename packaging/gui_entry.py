"""APU Pick 打包用的進入點（PyInstaller）。

multiprocessing.freeze_support() 一定要最先呼叫：打包後多核心量測的子行程跑的也是這個 exe，
少了它每個子行程都會再開一個視窗。

--smoke-test <資料夾> <結果檔>：確認打包好的 exe 能量測（含多核心）、畫圖，build_exe.py 打包完會自動跑。
結果檔旁邊會一起寫出同名的 .png 趨勢圖和 .csv 報表。
"""

import multiprocessing
import sys


def smoke_test(folder: str, out: str) -> int:
    import traceback
    from pathlib import Path

    result = Path(out)
    try:
        import matplotlib.backends.backend_tkagg  # noqa: F401  視窗介面用的東西都要有打包進來
        from astro_light_selector import gui  # noqa: F401
        from astro_light_selector.pipeline import collect_files, decide, measure_files
        from astro_light_selector.plot import plot_decisions
        from astro_light_selector.report import write_csv
        import rawpy  # 相機 RAW 支援要能載入（內含 LibRaw 動態函式庫）

        assert rawpy.libraw_version

        folder_path = Path(folder)
        files, home_of = collect_files(folder_path, folder_path / "rejected")
        frames = measure_files(files, workers=2, home_of=home_of)
        sel = decide(frames, ("filter", "exposure"))
        plot_decisions(sel.decisions, result.with_suffix(".png"), sel.thresholds)
        write_csv(sel.decisions, result.with_suffix(".csv"))
        # 預覽與拍攝資訊（astropy 的星曆、IERS 資料要有打包進來）
        from astropy.io import fits

        from astro_light_selector.preview import make_preview
        from astro_light_selector.sky import sky_info

        make_preview(files[0])
        sky = sky_info(fits.Header({"RA": 0.0, "DEC": 89.9999, "SITELAT": 23.5, "SITELONG": 120.5,
                                    "DATE-OBS": "2024-09-18T02:34:00"}))
        assert sky is not None and abs(sky.altitude - 23.5) < 0.5 and sky.moon_illum > 0.97, sky
        errors = sum(1 for f in frames if f.error)
        result.write_text(f"ok frames={len(frames)} keep={sel.n_keep} errors={errors}\n", encoding="utf-8")
        return 0
    except Exception:  # noqa: BLE001
        result.write_text(traceback.format_exc(), encoding="utf-8")
        return 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if len(sys.argv) == 4 and sys.argv[1] == "--smoke-test":
        sys.exit(smoke_test(sys.argv[2], sys.argv[3]))
    from astro_light_selector.gui import main

    sys.exit(main())
