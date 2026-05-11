import sqlite3
import json
from main import get_workflows, process_message_logic

workflows = get_workflows()
if not workflows:
    print("No workflows found.")
else:
    wf = workflows[0]
    print(f"Testing workflow {wf['name']} with {len(wf['rules'])} rules.")
    text = """🤖 AI Solana CTO
🏷️ Pandamic | PANDAMIC

📌 CA ETZKRf2VWzQKi5X3Lr8CzpLHREB5jRB1SWUGzXNGpump

🏛️ Dex: Pumpswap
📈 24h Volume: $363.16K
📊 Market Cap: $21.76K
💧 Liquidity: $11.76K
⏳ Token Age: 1h 46m"""
    try:
        res = process_message_logic(text, wf['rules'])
        print("Success:", res)
    except Exception as e:
        import traceback
        traceback.print_exc()
