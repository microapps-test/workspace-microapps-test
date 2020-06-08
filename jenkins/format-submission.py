#!/usr/bin/env python3

import argparse
import json
import requests
from requests.auth import HTTPBasicAuth
from requests.exceptions import HTTPError
import subprocess
import zipfile

# This script is run in Jenkins on a job triggered by the creation or update 
# of a JIRA "Submission" issue type on the MICROSUB project at issues.citrite.net
# 
# The script will format the issue contents and raise a PR so that a pull request
# can be created on GitHub where the microapp integration bundles are stored

# parse args to obtain credentials for Jira API
parser = argparse.ArgumentParser(description='Format the export to include extra metadata from third parties')
parser.add_argument('--issueId', type=str, required=True, help='id of the issue that triggered the build')
parser.add_argument('--svcacctPwd', type=str, required=True, help='password for the service account making call to Jira')
parser.add_argument('--svcacctName', type=str, required=True, help='service account name used for Jira API calls')
parser.add_argument('--githubApiKey', type=str, required=True, help='personal access token for GitHub API access')
parser.add_argument('--githubUsername', type=str, required=True, help='username of service account on GitHub')
parser.add_argument('--githubPwd', type=str, required=True, help='password for service account on GitHub')
args = parser.parse_args()

# JIRA VARIABLES:
# custom fields from submission on MICROSUB project at issues.citrite.net
# authorization and headers for GitHub Jira API calls
SVCACCT_NAME = args.svcacctName
SVCACCT_PWD = args.svcacctPwd
ISSUE_ID = args.issueId
AUTH = HTTPBasicAuth(SVCACCT_NAME, SVCACCT_PWD)
HOST = "https://issues.citrite.net"
SUPPORT_URL = "customfield_12635"
DOCUMENT_URL = "customfield_31030"
PRIVACY_URL = "customfield_31032"
TERMS_URL = "customfield_31031"
VENDOR = "customfield_31230" 
HEADERS = {'Content-Type': 'application/json'}
METADATA_FILE = "metadata.json"

# GITHUB VARIABLES:
# remote GitHub repo where submissions are pushed for review
GITHUB_USERNAME = args.githubUsername
GITHUB_PASSWORD = args.githubPwd
GITHUB_API_KEY = args.githubApiKey
BUNDLE_REPO='{}/workspace-microapps-test-bundles'.format(GITHUB_USERNAME)

# administrator to be contacted if the script fails
ADMIN = "christopher.smallwood@citrix.com"


def _jira_comment(comment, statusCode=1):
    payload = json.dumps( {
        "body": comment
    } )
    try:
        response = requests.post(
            url='{}/rest/api/2/issue/{}/comment'.format(HOST, ISSUE_ID),
            headers=HEADERS,
            auth=AUTH,
            data=payload
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print('Failed to write comment to Jira\n{}'.format(e))
    exit(statusCode)


def get_mapp_file(issueJson):
    # only allow one attachment and must be a .mapp file
    mappFile = ""
    numAttachments = len(issueJson['fields']['attachment'])
    if numAttachments > 1:
        _jira_comment("Only attach a single file."
        "Job will be submitted once extra files are removed.")
    elif numAttachments < 1:
        _jira_comment("must attach a .mapp file."
        "Job will be submitted once a .mapp file is attached.")
    else:
        mappFile = issueJson['fields']['attachment'][0]['filename']
        if not mappFile.endswith('.mapp'):
            _jira_comment("Attachment must be a .mapp file."
            "Job will be submitted once a .mapp file is attached.")
        url = issueJson['fields']['attachment'][0]['content']
        try:
            response = requests.get(
                url=url,
                auth=AUTH
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            _jira_comment('Failed to download attachment\n{}\n'
            'Please contact Jenkins Admin: {}'.format(e, ADMIN))
        with open(mappFile, 'wb') as fout:
            fout.write(response.content)
    return mappFile


def get_issue():
    try:
        response = requests.get(
            url='{}/rest/api/2/issue/{}'.format(HOST, ISSUE_ID),
            headers=HEADERS,
            auth=AUTH,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        _jira_comment('Failed to get Jira issue\n{}\n'
        'Please contact Jenkins Admin: {}'.format(e, ADMIN))
    else:
        return response.json()


def format_bundle(mappFile, privacyUrl, documentationUrl, termsOfUseUrl, supportUrl, vendor):
    # extract contents of .mapp file into temporary directory
    try:
        with zipfile.ZipFile(mappFile, 'r') as zin:
            tempDir = 'temp'
            subprocess.run('[ -d "{}" ] || mkdir "{}"'.format(tempDir, tempDir), shell=True, check=True)
            zin.extractall(tempDir)
    except subprocess.CalledProcessError as e:
        _jira_comment('Failed to create {} directory\n{}\n'
        'Please contact Jenkins Admin: {}'.format(tempDir, e, ADMIN))
    
    # edit the metadata file
    exportId = ""
    try:
        with open('{}/{}'.format(tempDir, METADATA_FILE), 'r+') as fin:
            jsoncontents = json.load(fin)
            jsoncontents['vendor'] = vendor
            jsoncontents.pop('tags')
            jsoncontents.update({'metadata': []})
            jsoncontents['metadata'].append({'tag': 'privacyUrl', 'value': privacyUrl})
            jsoncontents['metadata'].append({'tag': 'documentationUrl', 'value': documentationUrl})
            jsoncontents['metadata'].append({'tag': 'termsOfUseUrl', 'value': termsOfUseUrl})
            jsoncontents['metadata'].append({'tag': 'supportUrl', 'value': supportUrl})
            exportId = jsoncontents['id']
            fin.seek(0)
            json.dump(jsoncontents, fin, indent=4)
            fin.truncate()
    except IOError as e:
        _jira_comment('{} file not found in .mapp file\n{}\n'
        'Please contact Jenkins Admin: {}'.format(METADATA_FILE, e, ADMIN))
    except KeyError as e:
        _jira_comment('Malformed {} file. Missing key\n{}\n'
        'Please contact Jenkins Admin: {}'.format(METADATA_FILE, e, ADMIN))

    # place files in directory structure: ./http/<vendor>/<exportid> 
    try:
        httpDir = './http'
        vendorDir = '{}/{}'.format(httpDir, vendor)
        exportDir = '{}/{}/{}'.format(httpDir, vendor, exportId)
        subprocess.run('[ -d "{}" ] || mkdir "{}"'.format(httpDir, httpDir), shell=True, check=True)
        subprocess.run('[ -d "{}" ] || mkdir "{}"'.format(vendorDir, vendorDir), shell=True, check=True)
        subprocess.run('[ -d "{}" ] || mkdir "{}"'.format(exportDir, exportDir), shell=True, check=True)
        subprocess.run('rsync -a {}/ {}/'.format(tempDir, exportDir), shell=True, check=True)
        subprocess.run('mv {} {}'.format(mappFile, exportDir), shell=True, check=True)
    except subprocess.CalledProcessError as e:
        _jira_comment('Failed to create directory structure\n{}\n'
        'Please contact Jenkins Admin: {}'.format(e, ADMIN))
    
    # add files, create a git commit and push to branch
    try:
        subprocess.run('git add {}'.format(exportDir), shell=True, check=True)
        subprocess.run('git commit -sm "microapp submission - see issue {} on {}"'.format(
            ISSUE_ID, HOST), shell=True, check=True)
        subprocess.run('git push https://{}:{}@github.com/{}.git HEAD:{}'.format(
            GITHUB_USERNAME, GITHUB_PASSWORD, BUNDLE_REPO, ISSUE_ID), shell=True, check=True)
    except IOError as e:
        _jira_comment('{} file not found.\n{}\n'
        'Please contact Jenkins Admin: {}'.format(exportDir, e, ADMIN))
    except subprocess.CalledProcessError as e:
        _jira_comment('Failed to push to https://github.com/{}.git\n{}\n'
        'Please contact Jenkins Admin: {}'.format(BUNDLE_REPO, e, ADMIN))


def raise_pr():
    url='https://{}:{}@api.github.com/repos/{}/pulls'.format(GITHUB_USERNAME, GITHUB_API_KEY, BUNDLE_REPO)
    headers = {'Content-Type': 'application/json'}
    payload = json.dumps( {
        "title": "micoapp integration submission",
        "body": "Please review submission before merging to master",
        "head": ISSUE_ID,
        "base": "master"
    } )
    try:
        response = requests.post(
            url=url,
            headers=headers,
            data=payload
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        _jira_comment('Failed to create PR to bundle repo:\n{}\n'
        'Please contact Jenkins Admin: {}'.format(e, ADMIN))
    else:
        pullRequestUrl = response.json()["html_url"]
        _jira_comment('Jenkins job successfully created pull request.\n'
        'You can view the pull request at {}'.format(pullRequestUrl), 0)
        

def main():
    # create new or checkout existing branch
    try:
        subprocess.run('git fetch origin -a', shell=True, check=True)
        cmd = 'git branch -a | grep {} && git checkout --track origin/{} || git checkout -b {}'.format(
            ISSUE_ID, ISSUE_ID, ISSUE_ID)
        subprocess.run(cmd, shell=True, check=True)
    except subprocess.CalledProcessError as e:
        _jira_comment('Failed to create new branch on the bundle repo:\n{}'.format(e))

    # get the issue in json format and extract the necessary metadata
    issueJson = get_issue()
    supportUrl = issueJson['fields'][SUPPORT_URL]
    documentationUrl = issueJson['fields'][DOCUMENT_URL]
    privacyUrl = issueJson['fields'][PRIVACY_URL]
    termsOfUseUrl = issueJson['fields'][TERMS_URL]
    vendor = issueJson['fields'][VENDOR]

    # download .mapp file from Jira issue, format and raise a PR on GitHub
    mappFile = get_mapp_file(issueJson)
    format_bundle(mappFile, privacyUrl, documentationUrl, termsOfUseUrl, supportUrl, vendor)
    raise_pr()


if __name__ == "__main__":
    main()
