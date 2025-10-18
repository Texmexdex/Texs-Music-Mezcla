@echo off
echo Creating Python virtual environment in a folder named "venv"...
python -m venv venv
echo Activating the virtual environment and installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt
echo Setup complete. You can now run the application using run.bat.
pause