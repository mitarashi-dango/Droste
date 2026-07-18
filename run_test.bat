@echo off
chcp 65001 > nul
echo ===================================================
echo  Room Indicator Streamer (テスト用起動スクリプト)
echo ===================================================
echo.
echo Flaskサーバーを起動します。ブラウザは安全のため自動起動しません。
echo PCで設定する場合は http://localhost:5000/ を手動で開いてください。
echo.
python app.py
pause
