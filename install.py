#!/usr/bin/env python3
import shutil
import os

INSTALL_LIB_PATH = '/usr/lib/foopkg'
EXEC_SYMLINK_PATH = '/usr/bin/foopkg'
EXECUTABLE_NAME = 'foopkg.py'

if os.geteuid() != 0:
	raise PermissionError('You forgot a sudo. Please use one.')

def directories():
	os.makedirs(INSTALL_LIB_PATH, exist_ok=True, mode=0o755)

def files():
	shutil.copy(os.path.join(os.curdir, EXECUTABLE_NAME), INSTALL_LIB_PATH)
	shutil.copy(os.path.join(os.curdir, 'porgrc'), '/usr/local/etc')

def mysymlink(src, dst):
	if os.path.exists(dst):
		return
	else:
		os.symlink(src, dst)

def symlinks():
	mysymlink(os.path.join(INSTALL_LIB_PATH, EXECUTABLE_NAME), EXEC_SYMLINK_PATH)

if __name__ == '__main__':
	directories()
	files()
	symlinks()
