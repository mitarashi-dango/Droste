import traceback
import ctypes
import win32gui
import win32ui
import win32con
from PIL import Image

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

def capture_desktop_win32():
    hwnd = None
    hwndDC = None
    mfcDC = None
    saveDC = None
    saveBitMap = None
    try:
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(76) # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
        w = user32.GetSystemMetrics(78)    # SM_CXVIRTUALSCREEN
        h = user32.GetSystemMetrics(79)    # SM_CYVIRTUALSCREEN

        if w == 0 or h == 0:
            left = 0
            top = 0
            w = user32.GetSystemMetrics(0) # SM_CXSCREEN
            h = user32.GetSystemMetrics(1) # SM_CYSCREEN

        print(f"Desktop Rect: l={left}, t={top}, w={w}, h={h}")

        hwnd = 0
        hwndDC = win32gui.GetDC(0)
        mfcDC  = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()

        saveBitMap = win32ui.CreateBitmap()
        saveBitMap.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(saveBitMap)

        saveDC.BitBlt((0, 0), (w, h), mfcDC, (left, top), win32con.SRCCOPY)

        bmpinfo = saveBitMap.GetInfo()
        bmpstr = saveBitMap.GetBitmapBits(True)

        im = Image.frombuffer(
            'RGB',
            (bmpinfo['bmWidth'], bmpinfo['bmHeight']),
            bmpstr, 'raw', 'BGRX', 0, 1)

        print(f"Success capture_desktop_win32! Size: {im.size}")
        return im
    except Exception as e:
        print("capture_desktop_win32 failed.")
        traceback.print_exc()
        return None
    finally:
        if saveBitMap is not None:
            try:
                win32gui.DeleteObject(saveBitMap.GetHandle())
            except Exception:
                pass
        if saveDC is not None:
            try:
                saveDC.DeleteDC()
            except Exception:
                pass
        if mfcDC is not None:
            try:
                mfcDC.DeleteDC()
            except Exception:
                pass
        if hwndDC is not None:
            try:
                win32gui.ReleaseDC(0, hwndDC)
            except Exception:
                pass

if __name__ == "__main__":
    capture_desktop_win32()
