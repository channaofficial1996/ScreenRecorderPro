# Screen Recorder Pro

កម្មវិធីថតអេក្រង់សម្រាប់ Windows ប្រើដោយ**ឥតគិតថ្លៃ** ពី **Cambo Ads**។ ល្អសម្រាប់ថតមេរៀន។

## ទាញយក

ទាញយក `ScreenRecorderPro-Setup.exe` ពីទំព័រ [Releases](https://github.com/channaofficial1996/ScreenRecorderPro/releases/latest) រួចដំឡើង។ មិនត្រូវការសិទ្ធិ Administrator ទេ។ ក្រោយដំឡើង app មាន shortcut លើ Desktop ហើយរកឃើញក្នុង Windows Search។ បើចង់លុប ចូល Settings > Apps។

បើមិនចង់ដំឡើង អាចទាញយក `ScreenRecorderPro.exe` ហើយបើកបានភ្លាម។

នៅពេលមាន version ថ្មី ប៊ូតុង **Update** នៅខាងក្រោម app នឹងប្រែពណ៌បៃតង។ ចុចវា ហើយ app នឹងទាញយក និងបើកឡើងវិញដោយខ្លួនឯង។

## មុខងារ

- ថតពេញអេក្រង់ គ្រប់អេក្រង់ តំបន់ដែលជ្រើស ឬ window មួយ
- ថតសំឡេង microphone ពីរ និងសំឡេងកុំព្យូទ័រ ជាមួយការកាត់សំឡេងរំខាន
- Zoom ទៅចំណុចសំខាន់ពេលកំពុងថត (`1.5x` ដល់ `3x`) ហើយអាចតាម mouse
- Encode ដោយ GPU (NVIDIA, Intel, AMD) ឬ CPU
- Pause/Resume និង Screenshot

## Hotkeys

| Hotkey | មុខងារ |
| --- | --- |
| `Ctrl+Alt+R` | ចាប់ផ្ដើម / បញ្ឈប់ការថត |
| `Ctrl+Alt+P` | Pause / Resume |
| `Ctrl+Alt+S` | Screenshot |
| `Ctrl+Alt+Z` | Zoom ចូល / ចេញ |

## រត់ពីកូដ

ត្រូវការ Python 3.12 និង [FFmpeg](https://www.gyan.dev/ffmpeg/builds/)។ ដើម្បី build Setup ត្រូវការ [Inno Setup 6](https://jrsoftware.org/isinfo.php) បន្ថែម។

```
pip install numpy pyaudiowpatch customtkinter pillow pyinstaller
python screen_recorder.py
```

## ចេញ version ថ្មី

1. ប្ដូរ `APP_VERSION` នៅក្នុង `screen_recorder.py` ឧទាហរណ៍ `1.1.0`
2. Build: `python build.py`
3. Publish release ដែលមាន tag ត្រូវនឹង version។ ត្រូវដាក់ file ទាំងពីរ៖ `ScreenRecorderPro-Setup.exe` សម្រាប់អ្នកដំឡើងថ្មី និង `ScreenRecorderPro.exe` សម្រាប់ប៊ូតុង Update ក្នុង app។

```
gh release create v1.1.0 dist/ScreenRecorderPro-Setup.exe dist/ScreenRecorderPro.exe --title "v1.1.0" --notes "អ្វីដែលថ្មី..."
```

App ដែលអ្នកប្រើមានស្រាប់នឹងឃើញ version ថ្មីនេះនៅពេលបើកលើកក្រោយ។

## License

© 2026 Cambo Ads. ចែកចាយក្រោម [GNU AGPL-3.0](LICENSE)។

`ScreenRecorderPro.exe` មាន [FFmpeg](https://ffmpeg.org) ភ្ជាប់មកជាមួយ ដែលចែកចាយក្រោម GPL។ កូដប្រភពរបស់វាមាននៅ <https://ffmpeg.org/download.html>។
