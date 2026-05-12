# Runs twice

source .env.sh
# Produce and auto-approve two videos, then post them
python3 run_daily.py produce
python3 run_daily.py produce
python3 run_daily.py post
python3 run_daily.py post
