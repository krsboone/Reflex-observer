import psutil

proc_name = 'node'

def get_proc(proc_name):
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] == proc_name:
            return True
    return False

def result():
    if get_proc(proc_name) == True:
        print('Node is Running')
        #return('Node is Running')
    else:
        print('Node is DOWN')
        #return('Node is DOWN')

result()
