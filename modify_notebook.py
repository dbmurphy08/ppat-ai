import json
import os

file_path = 'Bar_Owner_Agent/agent.ipynb'

try:
    with open(file_path, 'r', encoding='utf-8') as f:
        notebook = json.load(f)

    google_accounts_modified = False
    prompt_modified = False

    for cell in notebook['cells']:
        if cell['cell_type'] == 'code':
            # Modify GOOGLE_ACCOUNTS
            new_source = []
            for line in cell['source']:
                if "GOOGLE_ACCOUNTS = ['personal', 'bar', 'personal_business']" in line:
                    new_source.append("GOOGLE_ACCOUNTS = ['personal', 'bar', 'personal_business', 'manager'] 
")
                    google_accounts_modified = True
                else:
                    new_source.append(line)
            cell['source'] = new_source

            # Modify system_instruction
            # We look for the cell containing the system prompt
            source_text = "".join(cell['source'])
            if 'system_instruction = """' in source_text:
                new_source_prompt = []
                for line in cell['source']:
                    # Update Goal
                    if 'Your Goal: Review the raw data and generate a "Daily Battle Plan".' in line:
                        new_source_prompt.append('        Your Goal: Review the raw data, generate a "Daily Battle Plan", and predict crowd size/strategy.\n')
                    # Insert Prediction Section
                    elif '## 📝 BRIEFING' in line:
                        new_source_prompt.append("        ## 📊 CROWD PREDICTION & STRATEGY\n")
                        new_source_prompt.append("        (Analyze the calendar for the next 24 hours. Look for events that drive traffic. Predict crowd size: Low/Medium/High. Recommend a strategy for staffing/inventory.)\n")
                        new_source_prompt.append("        \n")
                        new_source_prompt.append(line)
                    else:
                        new_source_prompt.append(line)
                
                if new_source_prompt != cell['source']:
                    cell['source'] = new_source_prompt
                    prompt_modified = True

    if google_accounts_modified:
        print("Successfully modified GOOGLE_ACCOUNTS.")
    else:
        print("WARNING: Could not find GOOGLE_ACCOUNTS line to modify.")

    if prompt_modified:
        print("Successfully modified system_instruction.")
    else:
        print("WARNING: Could not find system_instruction to modify.")

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(notebook, f, indent=1)

except Exception as e:
    print(f"Error: {e}")
