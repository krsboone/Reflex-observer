import psutil

# For a specific named process
proc_name = ['npm exec @pubnub/mcp@latest', '', '', '']

def get_proc(proc_name):
    for proc in psutil.process_iter(['cmdline']):
        if proc.info['cmdline'] == proc_name:
            return True
    return False

def result():
    if get_proc(proc_name) == True:
        print('MCP Server is UP')
        #return('MCP Server is UP')
    else:
        print('MCP Server is DOWN')
        #return('MCP Server is DOWN')

result()
