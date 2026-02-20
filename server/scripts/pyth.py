import psutil

proc_name = 'Python'

def get_proc(proc_name):
    for proc in psutil.process_iter(['name']):
        if proc.info['name'] == proc_name:
            return True
    return False

def result():
    if get_proc(proc_name) == True:
        print('Python is Running')
        #return('Python is Running')
    else:
        print('Python is DOWN')
        #return('Python is DOWN')

result()
