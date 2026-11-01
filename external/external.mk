# Out-of-tree packages live in package/<name>/<name>.mk (none yet).
include $(sort $(wildcard $(BR2_EXTERNAL_ECHO_PATH)/package/*/*.mk))
