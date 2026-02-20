import psutil

disk = psutil.disk_usage('/')

def get_avail(disk):
    print(round((disk.total - disk.free) / disk.total * 100, 2))
    #return(round((disk.total - disk.free) / disk.total * 100, 2))

get_avail(disk)
