# Runs twice

source .env.sh
# This runs the whole thing for ONE video each
python run_daily.py produce
python run_daily.py produce

# Open the approval UI
python approve_ui.py
# Visit http://localhost:5001/?token=$APPROVE_UI_TOKEN
# Approve a test video, then:
python run_daily.py post
python run_daily.py post
