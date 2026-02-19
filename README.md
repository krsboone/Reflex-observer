# Reflex-observer


## Details
Versatile, lightweight system observability

**PubNub**
Enable the following for the given keyset:
1. Presence
2. App Context
3. App Context -> Membership Events
4. File Sharing

**Server**
`dashboard.py` (webserver)
* Runs a webserver (http://localhost:8080)
* Reads config, passes values to the browser client (`dashboard.html`)
* Reads channel memberships to build a list of devices being monitored, passes to the browser client
* Compares the local test scripts (`scripts` dir) to the files shared within the `config_chan` Channel
* Handles adding of new files; save to local file system, then `send_file` to PubNub | Files sent to the `config_chan` Channel

`dashboard.html` (browser client)
* Subscribes to the channel each device will be Publishing test results to
* Listens for new Channel Memberships
* Visual display of test results by device
* Ability to add new scripts that will be executed by devices | opens local file picker, then `POST` the file to the webserver `api/upload-script`
* Ability to enable / disable scripts that are being run on each device | Publish command to the `ignore_chan` channel
* Ability to refresh the list of scripts being ignored by each device | Publish command to the `ignore_chan` channel

**Client**
`reflex-client.py` (monitor client)
* Reads config
* Subscribes to `config_chan` | used for receiving test files that the monitor client should run
* Subscribes to `ignore_chan` channel | used for commands sent from the browser client (refresh list, ignore tests) to the monitor client
* Initializes Channel meta data | Channel memberships are how the system knows what devices are being monitored
* Adds or removes Membership based on startup argument (join/exit)
* Sets a list of test scripts that should be ignored 
* Publishes a list of the tests being ignored to its device channel | every device has its own unique channel where test results are published to
* Reads local scripts then begins executing tests and Publishing results to its device channel
* This app will only ever Publish to its device channel

## Config
In the `config.ini` for `server/server-config` & `client/client-config` replace
```
pub = pub-key-here
sub = sub-key-here
```
with your pub/sub keys

## Flow
*Messages*
`config_chan` & `ignore_chan` -->(Subscribe) monitor client (Publish)--> device channel
device channel(s) -->(Subscribe) browser client (Publish)--> `ignore_chan`

*Files*
local file picker -->(Select) browser client (POST)--> `api/upload-script`
`api/upload-script` -->(Fetch) webserver (Save)--> `scripts` dir
uploaded files -->(List) webserver (send_file)--> `config_chan`

*Memberships*
statup arguments (join/exit) -->(Parse) monitor client (set/remove)--> `channel` Channel membership
`/api/members` -->(GET) browser client (createElement)--> Create device card
list `channel` members -->(get_channel_members) webserver (respond)--> `/api/members`
membership events -->(Listen) browser client (createElement)--> Create device card


## Todo
1. Add ability to update the test interval for individual device
2. Add ability to update the test interval for all devices
3. Add the ability to update the ignored tests for all devices
4. Add ability to delete a host (remove membership) from dashboard
5. Add ability to display individual test results in a time value style graph
6. Consideration for File Sharing retention period. If server is running continuously, will need to resend the files, or rerun the list_files & compare flow periodically
7. Change device channel naming convention to device.($hostname} so wildcard subscribe can be used on the server
8. `channel` var to config file
