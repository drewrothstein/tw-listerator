#!/usr/bin/env python
"""Twitter Listerator"""

import base64
import csv
import datetime
import logging
import os
import time

import cloudstorage as gcs                     # noqa: I201
from flask import Flask                        # noqa: I201
from google.appengine.api import app_identity  # noqa: I201
from google.appengine.api import urlfetch      # noqa: I201
from google.cloud import storage               # noqa: I201
import googleapiclient.discovery               # noqa: I201
import tweepy                                  # noqa: I201
import yaml                                    # noqa: I201


DEBUG               = False                # noqa: E221
SAVE_TO_GCS         = True                 # noqa: E221
GCS_EXPORT_BUCKET   = 'twitter-friends'    # noqa: E221
GCS_EXPORT_FILENAME = 'twitter-friends'    # noqa: E221
LIST_NAME           = 'Synced Friends'     # noqa: E221

# KMS
KMS_BUCKET          = '...'                # noqa: E221
KMS_LOCATION        = 'global'             # noqa: E221
KMS_KEYRING         = '...'                # noqa: E221
TW_CRYPTOKEY        = 'twitter'            # noqa: E221
TW_API_FILE         = 'twitter.encrypted'  # noqa: E221


urlfetch.set_default_fetch_deadline(60)
app = Flask(__name__)

# Set reasonable log levels
logging.getLogger('oauthlib').setLevel(logging.ERROR)
logging.getLogger('oauthlib.oauth1').setLevel(logging.ERROR)
logging.getLogger('requests-oauthlib').setLevel(logging.ERROR)
logging.getLogger('requests_oauthlib').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)


def _decrypt(project_id, location, keyring, cryptokey, cipher_text):
    """Decrypts and returns string from given cipher text."""
    logging.info('Decrypting cryptokey: {}'.format(cryptokey))
    kms_client = googleapiclient.discovery.build('cloudkms', 'v1')
    name = 'projects/{}/locations/{}/keyRings/{}/cryptoKeys/{}'.format(
        project_id, location, keyring, cryptokey)
    cryptokeys = kms_client.projects().locations().keyRings().cryptoKeys()
    request = cryptokeys.decrypt(
        name=name,
        body={'ciphertext': base64.b64encode(cipher_text).decode('ascii')})
    response = request.execute()
    return base64.b64decode(response['plaintext'].encode('ascii'))


def _download_output(output_bucket, filename):
    """Downloads the output file from GCS and returns it as a string."""
    logging.info('Downloading output file')
    client = storage.Client()
    bucket = client.get_bucket(output_bucket)
    output_blob = (
        'keys/{}'
        .format(filename))
    return bucket.blob(output_blob).download_as_string()


def get_credentials(cryptokey, filename):
    """Fetches credentials from KMS returning a decrypted API key."""
    credentials_enc = _download_output(KMS_BUCKET, filename)
    credentials_dec = _decrypt(app_identity.get_application_id(),
                               KMS_LOCATION,
                               KMS_KEYRING,
                               cryptokey,
                               credentials_enc)
    credentials_dec_yaml = yaml.load(credentials_dec)
    return credentials_dec_yaml


def chunker(thelist, segsize):
    """Chunks lists into a given segment size."""
    for x in range(0, len(thelist), segsize):
        yield thelist[x:x+segsize]


def setup_api(keys):
    """Setup Twitter API.

    Args:
        keys: Twitter API credentials.

    Returns:
        An authenticated `api` Class.
    """
    logging.info('Setting up Twitter API access')
    auth = tweepy.OAuthHandler(keys['consumer_key'], keys['consumer_secret'])
    auth.set_access_token(keys['access_token'], keys['access_token_secret'])
    api = tweepy.API(auth)
    return api


def limit_handled(cursor):
    """Default rate limit handler for Twitter."""
    while True:
        try:
            yield cursor.next()
        except tweepy.RateLimitError:
            time.sleep(15 * 60)


def is_valid_user(api, user_id):
    """Checked if `user_id` is a valid user. It is possible (and occurs) that
    a `user_id` may no longer be valid. This is assumed (best guess) that this
    takes place when a user deletes their account.

    Args:
        api: An authenticated `api` Class.
        user_id: An id for a single user.

    Returns:
        True if a valid user, False if not a valid user.
    """
    logging.info('Checking validity of user: {0}'.format(user_id))

    try:
        api.get_user(user_id=user_id)
        return True
    except tweepy.TweepError as error:
        logging.error('Unable to get user {0} with error: {1}'.format(
            user_id, error))
        return False


def create_list(api):
    """Creates list if it does not exist.

    Args:
        api: An authenticated `api` Class.

    Returns:
        Twitter list id as a string.
    """
    logging.info('Checking and creating list')
    list_id = None
    for _list in api.lists_all():
        if _list.name == LIST_NAME:
            list_id = _list.id_str

    if not list_id:
        _list = api.create_list(name=LIST_NAME, mode='private',
                                description='Synced list of friends from tw-listerator')  # noqa: E501
        list_id = _list.id_str

    return list_id


def get_friends(api):
    """Gets friends (accounts that you follow).

    Args:
        api: An authenticated `api` Class.

    Returns:
        List of friends.
    """
    logging.info('Getting friends')
    if api.me().friends_count > 5000:
        logging.warning('You have over 5,000 friends and only the first 5,000 will be synced')  # noqa: E501

    friends = []
    for friend in limit_handled(tweepy.Cursor(api.friends_ids, id=api.me().id).items()):  # noqa: E501
        friends.append(friend)

    logging.debug('Fetched {0} friend IDs'.format(len(friends)))
    return friends


def get_friends_in_list(api, list_id):
    """Gets current friends in list.

    Args:
        api: An authenticated `api` Class.

    Returns:
        List of friends that are currently in the list.
    """
    logging.info('Getting friends currently in list')
    friends_in_list = []
    for friend in limit_handled(tweepy.Cursor(api.list_members, list_id=list_id).items()):  # noqa: E501
        friends_in_list.append(friend.id)

    return friends_in_list


def sync_friends_to_list(api, friends, friends_in_list, list_id):
    """Syncs friends to list adding and removing as needed."""
    logging.info('Syncing friends to list')

    friends_to_add_to_list = []
    friends_to_remove_from_list = []

    # Add friends if not in list
    for friend in friends:
        if friend not in friends_in_list:
            if is_valid_user(api, friend):
                friends_to_add_to_list.append(friend)

    # Remove friend if in list and no longer friends
    for friend in friends_in_list:
        if friend not in friends:
            if is_valid_user(api, friend):
                friends_to_remove_from_list.append(friend)

    logging.info('Adding {0} friend(s) to list'.format(len(friends_to_add_to_list)))  # noqa: E501
    for friends_chunk in chunker(friends_to_add_to_list, 100):
        api.add_list_members(user_id=friends_chunk, list_id=list_id)
        logging.info('Added {0} friend(s) to list'.format(len(friends_chunk)))

    logging.info('Removing {0} friend(s) from list'.format(len(friends_to_remove_from_list)))  # noqa: E501
    for friends_chunk in chunker(friends_to_remove_from_list, 100):
        api.remove_list_members(user_id=friends_chunk, list_id=list_id)
        logging.info('Removed {0} friend(s) from list'.format(len(friends_chunk)))  # noqa: E501

    return


def save_friends_to_gcs(api, friends):
    """Saves your list of friend IDs to GCS as a CSV."""
    logging.info('Uploading data to GCS')
    now_iso8601 = datetime.datetime.utcnow().isoformat('T')

    filename_to_create = '{0}/{1}.csv'.format(now_iso8601, GCS_EXPORT_FILENAME)
    bucket_with_filename = os.path.join('/', GCS_EXPORT_BUCKET, filename_to_create)  # noqa: E501

    try:
        with gcs.open(bucket_with_filename, 'w',
                      content_type='text/csv') as gcs_file:

            writer = csv.writer(gcs_file,
                                delimiter=',', quotechar='"', quoting=csv.QUOTE_ALL)  # noqa: E501
            for friend in friends:
                writer.writerow([friend])
    except Exception as error:
        logging.error('An error occurred writing the file to GCS: {0}'.format(error))  # noqa: E501
        raise error


def runit():
    """Runs the task."""
    tw_creds        = get_credentials(TW_CRYPTOKEY, TW_API_FILE)  # noqa: E221
    api             = setup_api(tw_creds)                         # noqa: E221
    list_id         = create_list(api)                            # noqa: E221
    friends         = get_friends(api)                            # noqa: E221
    friends_in_list = get_friends_in_list(api, list_id)           # noqa: E221
    sync_friends_to_list(api, friends, friends_in_list, list_id)

    if SAVE_TO_GCS:
        save_friends_to_gcs(api, friends)

    return 'Completed'


@app.route('/run')
def run():
    return runit()


@app.errorhandler(500)
def server_error(e):
    # Log the error and stacktrace.
    logging.exception('An error occurred during a request.')
    return 'An internal error occurred.', 500
