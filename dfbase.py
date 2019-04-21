#!/usr/bin/python3
# -*- coding:utf-8 -*-
__author__  = "Kim, Taehoon(kimfrancesco@gmail.com)"

import os
import sys
import stat
import time
import json
import hashlib
from subprocess import Popen, PIPE
from dflogging import *


DOCKER_INSPECT_CMD = "docker inspect {}"
DOCKER_TOP_CMD = "docker top {} -eo user,pid,ppid,stime,command"
READLINK_CMD = "readlink  {}"

LOG_JOURNALD = "journalctl -u docker -o json > {}/jouranld_docker.json"

AUFS_IMAGE_BASE_PATH = "/var/lib/docker/aufs/"
AUFS_IMAGE_LAYERDB_PATH = "/var/lib/docker/image/aufs/layerdb/mounts/"
AUFS_WHITEOUT_PREFIX = ".wh."



class DFbase():

    def __init__(self):
        self.storage_driver = str()
        self.pid = int()
        self.data = dict()

        self.IS_OVERLAYFS = False
        self.IS_AUFSFS = False
        self.overlay_merged_path = str()
        self.aufs_mnt_path = str()
        self.aufs_container_branch_path = str()
        self.aufs_container_layerdb_path = str()

        df_log_initialize()

    def check_privilege(self):
        log.debug('run with GETUID:{}'.format(os.getuid()))
        return True if (os.getuid() == 0) else False

    def get_details_using_inspect_command(self, container_id):

        try:
            p = Popen(DOCKER_INSPECT_CMD.format(container_id), shell=True, stdout=PIPE, stderr=PIPE)
            data_dump, stderr_data = p.communicate()

            # print(json.dumps(json.loads(data_dump), indent=4))

        except Exception as e:
            print(e)
            return False

        self.data = json.loads(data_dump.decode('utf-8'))
        self.storage_driver = self.data[0]['Driver']
        self.pid = self.data[0]['State']['Pid']
        self.container_id = self.data[0]['Id']

        log.debug('storage_driver:{}'.format(self.storage_driver))
        if self.storage_driver == 'overlay2' or self.storage_driver == 'overlay':
            self.IS_OVERLAYFS = True
            self.overlay_upperdir_path = self.data[0]['GraphDriver']['Data']['UpperDir']
            self.overlay_merged_path = self.data[0]['GraphDriver']['Data']['MergedDir']
            log.debug('Driver:{}, UppderDir:{}'.format(self.storage_driver, self.overlay_merged_path))
            log.debug('Driver:{}, UppderDir:{}'.format(self.storage_driver, self.overlay_upperdir_path))
        elif self.storage_driver == 'aufs':
            self.IS_AUFSFS = True
            self.aufs_container_layerdb_path = AUFS_IMAGE_LAYERDB_PATH + self.data[0]['Id']
            log.debug('Driver:{}, ContainerDir:{}'.format(self.storage_driver,
                                                          '/var/lib/docker/containers/' + self.data[0]['Id']))
        else:
            print('Not support')
            pass

        log.debug("container id : {}".format(self.container_id))


    def setup_config(self):

        self.artifacts_path = str()
        self.executable_path = str()

        try:
            with open('config.json') as f:
                config = json.load(f)
        except Exception as e:
            print(e)
            log.debug(e)
            return False

        self.artifacts_path = config['ARTIFACTS']['BASE_PATH'].format(self.container_id)
        self.executable_path = config['ARTIFACTS']['EXECUTABLE_PATH']
        self.executable_path = self.executable_path.replace('BASE_PATH', self.artifacts_path)
        self.log_journald = (True if config['ARTIFACTS']['LOG_JOURNALD_SERVICE'] == "TRUE" else False)

        if not os.path.exists(self.artifacts_path):
            os.makedirs(self.artifacts_path, mode=0o700)
        elif not os.path.isdir(self.artifacts_path):
            log.debug('[Error]' + self.artifacts_path +' is not a directory')
            return False

        if not os.path.exists(self.executable_path):
            os.makedirs(self.executable_path, mode=0o700)
        elif not os.path.isdir(self.executable_path):
            log.debug('[Error]' + self.executable_path +' is not a directory')
            return False

    def save_inspect_for_container(self):
        inspect_output = self.artifacts_path + '/' + 'inspect_command.json'
        with open(inspect_output, 'w') as f:
            json.dump(self.data, f, indent=4)

    def get_processes_list_within_container(self):
        '''
        [
            {
                "Id": "31df7fe258be4c78ca0a15cd7903ead5f4ba3ff3f63a0755a3da00fbc39e2ea2",
                "Created": "2018-08-25T08:18:27.596162472Z",
                "Path": "nginx",
                "Args": [
                     "-g",
                    "daemon off;"
                ],
                "State": {
                    "Status": "running",
                    "Pid": 3691,
                    "StartedAt": "2018-08-26T12:25:09.283556466Z",
                    "FinishedAt": "2018-08-26T12:23:24.546575093Z"
            },

        '''
        items_list = list()
        proc_item = list()
        procs_dict = dict()

        p = Popen(DOCKER_TOP_CMD.format(self.container_id), shell=True, stdout=PIPE, stderr=PIPE)
        stdout_dump, stderr_data = p.communicate()

        procs_lines = stdout_dump.decode('utf-8')
        procs_lines = procs_lines.split("\n")
        #procs_lines = procs_lines.split()

        for procs_item in procs_lines:
            if 'USER' in procs_item:
                continue
            elif len(procs_item):
                proc_item.append(procs_item)

        for item in proc_item:
            x = item.split(None, 4)
            log.debug('PID:{}, {}, {}, {}, {}'.format(x[0], x[1],x[2],x[3],x[4]))
            procs_dict['USER'] = x[0]
            procs_dict['PID'] = x[1]
            procs_dict['PPID'] = x[2]
            procs_dict['STIME'] = x[3]
            procs_dict['CMD'] = x[4]

            items_list.append(procs_dict.copy())

        #print(items_list)

        procs_path = self.artifacts_path + '/' + 'top_command.json'
        with open(procs_path, 'w') as f:
            json.dump(items_list, f, indent=4)

        self.copy_executable(items_list)


    def copy_executable(self, procs_list):
        proc_list = []
        md5sum = str()
        for proc in procs_list:
            proc_path = '/proc/' + proc.get('PID') + '/exe'
            p = Popen(READLINK_CMD.format(proc_path), shell=True, stdout=PIPE, stderr=PIPE)
            stdout_dump, stderr_data = p.communicate()
            exe_path = stdout_dump.decode('utf-8')

            if exe_path and self.IS_OVERLAYFS:
                if os.path.isfile('{}{}'.format(self.overlay_merged_path, exe_path.strip('\n'))):
                    md5sum = self.get_md5sum('{}{}'.format(self.overlay_merged_path, exe_path.strip('\n')))
                    log.debug('md5sum:{}'.format(md5sum))
                    COPY_CMD = 'cp -f {}{} {}{}_{}'.format(self.overlay_merged_path, exe_path.strip('\n'), self.executable_path,exe_path.rsplit('/', 1)[1].strip('\n'), md5sum)
                    log.debug('PID:{}, CMD:{}'.format(proc.get('PID'), COPY_CMD))
                    os.system(COPY_CMD)
                    proc['EXECUTABLE'] = '{}{}'.format(self.overlay_merged_path, exe_path.strip('\n'))
                else:
                    proc['EXECUTABLE'] = 'NOT FOUND - {}{}'.format(self.overlay_merged_path, exe_path.strip('\n'))

            elif exe_path and self.IS_AUFSFS:
                self.aufs_mnt_path = self.get_aufs_container_mnt_path()
                if os.path.isfile('{}{}'.format(self.aufs_mnt_path, exe_path.strip('\n'))):
                    self.aufs_mnt_path = self.get_aufs_container_mnt_path()
                    md5sum = self.get_md5sum('{}{}'.format(self.aufs_mnt_path,  exe_path.strip('\n')))
                    log.debug('md5sum:{}'.format(md5sum))
                    COPY_CMD = 'cp -f {}{} {}{}_{}'.format(self.aufs_mnt_path, exe_path.strip('\n'), self.executable_path, exe_path.rsplit('/', 1)[1].strip('\n'), md5sum)
                    log.debug('PID:{}, CMD:{}'.format(proc.get('PID'), COPY_CMD))
                    os.system(COPY_CMD)
                    proc['EXECUTABLE'] = '{}{}'.format(self.aufs_mnt_path, exe_path.strip('\n'))
                else:
                    proc['EXECUTABLE'] = 'NOT FOUND - {}{}'.format(self.aufs_mnt_path, exe_path.strip('\n'))

            proc['MD5'] = md5sum
            proc_list.append(proc.copy())

        procs_path = self.artifacts_path + '/' + 'processes_running.json'
        with open(procs_path, 'w') as f:
            json.dump(proc_list, f, indent=4)


    def get_aufs_container_mnt_path(self):
        mountid_file = self.aufs_container_layerdb_path + '/mount-id'
        with open(mountid_file, 'r') as fd:
            line = fd.readline()
        return AUFS_IMAGE_BASE_PATH + 'mnt/' + line

    def get_md5sum(self, filepath):
        log.debug('md5sum target file:{}'.format(filepath))
        md5sum = hashlib.md5()
        try:
            with open(filepath, 'rb') as f:
                while True:
                    data = f.read(4096)
                    if not data:
                        break
                    md5sum.update(data)
        except Exception as e:
            print(e)
            return False

        return md5sum.hexdigest()

    def search_whiteout_files(self):
        if self.IS_OVERLAYFS:
            path = self.get_overlay_upperlayer_path()
            self.search_files_with_character_device(path)
        elif self.IS_AUFSFS:
            path = self.get_aufs_container_branch_path()
            self.search_files_with_wh_prefix(path)

    def get_overlay_upperlayer_path(self):
        return self.overlay_upperdir_path

    def get_aufs_container_branch_path(self):
        mountid_file = self.aufs_container_layerdb_path + '/mount-id'
        with open(mountid_file, 'r') as fd:
            line = fd.readline()
        return AUFS_IMAGE_BASE_PATH + 'diff/' + line

    def search_files_with_character_device(self, arg_path):
        overlay_wh_list = []
        overlay_whiteout = {}
        for dirpath, dirs, files in os.walk(arg_path):
            for filename in files:
                fname = os.path.join(dirpath, filename)
                mode = os.stat(fname).st_mode
                if stat.S_ISCHR(mode):
                    print('[Found] Character device file: {}, mtime:{}, size:{}'.format(fname, time.ctime(
                        os.stat(fname).st_mtime), os.stat(fname).st_size))
                    overlay_whiteout['file_type'] = 'CHARDEV'
                    overlay_whiteout['fname'] = fname
                    overlay_whiteout['mtime'] = time.ctime(os.stat(fname).st_mtime)
                    overlay_whiteout['size'] = os.stat(fname).st_size
                    overlay_wh_list.append(overlay_whiteout.copy())

        overlay_wh_path = self.artifacts_path + '/' + 'whiteout.json'
        if len(overlay_wh_list):
            with open(overlay_wh_path, 'w') as f:
                json.dump(overlay_wh_list, f, indent=4)

    def search_files_with_wh_prefix(self, arg_path):
        aufs_wh_list = []
        aufs_whiteout = {}
        for dirpath, dirs, files in os.walk(arg_path):
            for filename in files:
                if filename.startswith(AUFS_WHITEOUT_PREFIX):
                    fname = os.path.join(dirpath, filename)
                    print('[Found] WhiteOut(.wh.*) files: {}, mtime:{}, size:{}'.format(fname, time.ctime(
                        os.stat(fname).st_mtime), os.stat(fname).st_size))
                    aufs_whiteout['file_type'] = 'FILE'
                    aufs_whiteout['fname'] = fname
                    aufs_whiteout['mtime'] = time.ctime(os.stat(fname).st_mtime)
                    aufs_whiteout['size'] = os.stat(fname).st_size
                    aufs_wh_list.append(aufs_whiteout.copy())


            for dir in dirs:
                if dir.startswith(AUFS_WHITEOUT_PREFIX):
                    dirname = os.path.join(dirpath, dir)
                    print('[Found] WhiteOut(.wh.*) Directories: {}, mtime:{}, size:{}'.format(dirname, time.ctime(
                        os.stat(dirname).st_mtime), os.stat(dirname).st_size))
                    aufs_whiteout['file_type'] = 'DIRECTORY'
                    aufs_whiteout['fname'] = dirname
                    aufs_whiteout['mtime'] = time.ctime(os.stat(dirname).st_mtime)
                    aufs_whiteout['size'] = os.stat(dirname).st_size
                    aufs_wh_list.append(aufs_whiteout.copy())

        aufs_wh_path = self.artifacts_path + '/' + 'whiteout.json'
        if len(aufs_wh_list):
            with open(aufs_wh_path, 'w') as f:
                json.dump(aufs_wh_list, f, indent=4)


    def copy_files_relatedto_container(self):
        container_path = "/var/lib/docker/containers/{}".format(self.container_id)

        for dirpath, dirs, files in os.walk(container_path):
            for file in files:
                log.debug("dirpath:{}, dirs:{}, files:{}".format(dirpath, dirs, files))
                COPY_CMD = 'cp -f {}/{} {}'.format(dirpath, file, self.artifacts_path)
                os.system(COPY_CMD)

    def get_log_on_journald_service(self):
        if self.log_journald is False:
            print('This docker host does not use journald system')
            return False

        p = Popen(LOG_JOURNALD.format(self.artifacts_path), shell=True, stdout=PIPE, stderr=PIPE)
        stdout_dump, stderr_data = p.communicate()

