#!/usr/bin/env python
#
# CustomScript extension
#
# Copyright 2014 Microsoft Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Offline regression excerpts from Azure/azure-linux-extensions, commit
# ce24c534872610dab4a802735bb64056d81d700f, CustomScript/customscript.py.
# Function bodies are unmodified. Dependencies are stubbed by the test harness.

def download_files(hutil):
    public_settings = hutil.get_public_settings()
    if public_settings is None:
        raise ValueError("Public configuration couldn't be None.")
    cmd = get_command_to_execute(hutil)
    blob_uris = public_settings.get('fileUris')

    protected_settings = hutil.get_protected_settings()
    storage_account_name = None
    storage_account_key = None
    if protected_settings:
        storage_account_name = protected_settings.get("storageAccountName")
        storage_account_key = protected_settings.get("storageAccountKey")
        if storage_account_name is not None:
            storage_account_name = storage_account_name.strip()
        if storage_account_key is not None:
            storage_account_key = storage_account_key.strip()

    if (not blob_uris or not isinstance(blob_uris, list) or len(blob_uris) == 0):
        error_msg = "fileUris value provided is empty or invalid."
        hutil.log(error_msg + " Continue with executing command...")
        waagent.AddExtensionEvent(name=ExtensionShortName,
                                  op=DownloadOp,
                                  isSuccess=False,
                                  version=hutil.get_extension_version(),
                                  message="(01001)"+error_msg)
        return

    hutil.do_status_report('Downloading','transitioning', '0',
                           'Downloading files...')

    if storage_account_name and storage_account_key:
        hutil.log("Downloading scripts from azure storage...")
        download_blobs(storage_account_name,
                       storage_account_key,
                       blob_uris,
                       cmd,
                       hutil)
    elif not(storage_account_name or storage_account_key):
        hutil.log("No azure storage account and key specified in protected "
                  "settings. Downloading scripts from external links...")
        download_external_files(blob_uris, cmd, hutil)
    else:
        #Storage account and key should appear in pairs
        error_msg = "Azure storage account and key should appear in pairs."
        hutil.error(error_msg)
        waagent.AddExtensionEvent(name=ExtensionShortName,
                                  op=DownloadOp,
                                  isSuccess=False,
                                  version=hutil.get_extension_version(),
                                  message="(01000)"+error_msg)
        raise ValueError(error_msg)


def preprocess_files(file_path, hutil):
    """
        The file is preprocessed if it satisfies any of the following
        condistions:
            the file's extension is '.sh' or '.py'
            the content of the file starts with '#!'
    """
    ret = to_process(file_path)
    if ret:
        dos2unix(file_path)
        hutil.log("Converting {0} from DOS to Unix formats: Done".format(file_path))
        remove_bom(file_path)
        hutil.log("Removing BOM of {0}: Done".format(file_path))


def to_process(file_path, extensions=['.sh', ".py"]):
    for extension in extensions:
        if file_path.endswith(extension):
            return True
    with open(file_path, 'rb') as f:
        contents = f.read(64)
    if b'#!' in contents:
        return True
    return False


def dos2unix(file_path):
    with open(file_path, 'rU') as f:
        contents = f.read()
    temp_file_path = file_path + ".tmp"
    with open(temp_file_path, 'wb') as f_temp:
        f_temp.write(contents.encode())
    shutil.move(temp_file_path, file_path)
