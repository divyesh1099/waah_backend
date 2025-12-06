
with open("error.log", "r") as f:
    content = f.read()
    idx = content.find('column "')
    if idx != -1:
        print(content[idx:idx+80])
    else:
        print("substring not found in:")
        print(content[:100])
