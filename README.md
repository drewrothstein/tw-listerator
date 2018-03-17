# Twitter Listerator

Creates a private **list** on Twitter from the accounts you are **following**.  Keeps it up-to-date based-on the accounts that you follow.

A list could be used for purposes such as an alternate (and chronological!) timeline, better management / organization, et cetera.

Optionally writes the accounts that you are following to Google Cloud Storage (GCS) which is useful for debugging and searching externally to this program.

## Overview Diagram

![overview diagram](https://github.com/drewrothstein/tw-listerator/raw/master/errata/tw-listerator.png)

## Scheduled Cron Job

![scheduled cron](https://github.com/drewrothstein/tw-listerator/raw/master/errata/scheduled_cron.png)

## Stackdriver Logs

![stackdriver logs](https://github.com/drewrothstein/tw-listerator/raw/master/errata/stackdriver_logs.png)

## Twitter Synced List

![twitter list](https://github.com/drewrothstein/tw-listerator/raw/master/errata/twitter_list.png)

## What does it do?

It checks to see if the syncing list is created and if it isn't, it creates it.

Gets a list of the accounts you are following and adds them to the list.

Optionally writes the accounts you are following to GCS.

## Where does this run?

This is built to run on the Google App Engine Standard Environment as a Scheduled Task.

## How does it work?

It queries the lists that you already have to see if you have one that matches the syncing list's name with the `lists/list` endpoint ([doc](https://developer.twitter.com/en/docs/accounts-and-users/create-manage-lists/api-reference/get-lists-list)).  If the list does not exist, it creates one with the `lists/create` endpoint ([doc](https://developer.twitter.com/en/docs/accounts-and-users/create-manage-lists/api-reference/post-lists-create)).

It queries the accounts you are following (aka. your friends) with the `friends/ids` endpoint ([doc](https://developer.twitter.com/en/docs/accounts-and-users/follow-search-get-users/api-reference/get-friends-ids)).

Lastly, it adds users to the list with the `lists/members/create_all` endpoint([doc](https://developer.twitter.com/en/docs/accounts-and-users/create-manage-lists/api-reference/post-lists-members-create_all)).

## Dependencies

See the `requirements.txt` for the list of Python package dependencies.

This relies on successful responses from the Twitter APIs.

This is built to operate on Google App Engine and thus has dependencies on all of the relevant underlying infrastructure on Google Cloud Platform.

Google Cloud Platform Service Dependencies:
1) App Engine (Standard Environment)
2) Cloud Storage
3) Key Management Service
4) Logging via Stackdriver (not critical)

## Prerequisites

### Accounts

1. Twitter Account + Application Credentials ([apps.twitter.com](https://apps.twitter.com/))
2. Google Cloud Platform Account ([console.cloud.google.com](https://console.cloud.google.com/)).

### System

1. Python 2.7.
2. Working `pip` installation.
3. Installation of `gcloud` SDK and the `dev_appserver.py` loaded into your `PATH` ([doc](https://cloud.google.com/sdk/)).

## Configuration

### Cron Schedule

See `cron.yaml`.

### Secure Key Storage

To securely store the Twitter API credentials for access by the service from Google App Engine I have chosen to use Google's Key Management Service. Two initial one-time steps need to be completed for this to work.

1) Encrypt and upload the secrets to Google's Key Management Service.
2) Grant the appropriate Service Account access to decrypt the secrets.

Fetch your Twitter API credentials to be able to proceed.

1) Encrypt Secrets

We will create a Service Account in Google IAM to be able to encrypt / decrypt our secrets (which you could create seaparate encrypt/decrypt accounts and permissions if you would like).

To create a Service Account:
```
$ gcloud --project PROJECT_ID iam service-accounts create SERVICE_ACCOUNT_NAME
$ gcloud --project PROJECT_ID iam service-accounts keys create key.json \
--iam-account SERVICE_ACCOUNT_NAME@PROJECT_ID.iam.gserviceaccount.com
```

This creates a Service Account and a JSON file with the credentials which we can use to encrypt / decrypt our secrets outside of KMS.

One of the easiest ways to interact with Google KMS is to start with the samples from the GCP Samples [Here](https://github.com/GoogleCloudPlatform/python-docs-samples). Once you have this repository cloned, you will create a keyring and cryptokey:
```
$ gcloud --project PROJECT_ID kms keyrings create KEYRING_NAME --location global

$ gcloud --project PROJECT_ID kms keys create twitter --location global --keyring KEYRING_NAME --purpose encryption

$ gcloud --project PROJECT_ID kms keys add-iam-policy-binding twitter --location global \
--keyring KEYRING_NAME --member serviceAccount:SERVICE_ACCOUNT_NAME@PROJECT_ID.iam.gserviceaccount.com \
--role roles/cloudkms.cryptoKeyEncrypterDecrypter
```

You will also need to grant the project service account access to decrypt the keys for this implementation. You could use a more secure setup if you would like.
```
gcloud --project PROJECT_ID kms keys add-iam-policy-binding twitter --location global \
--keyring KEYRING_NAME --member serviceAccount:PROJECT_ID@appspot.gserviceaccount.com \
--role roles/cloudkms.cryptoKeyDecrypter
```

If you haven't used the KMS service before the SDK will error with a URL to go to to enable:
```
$ gcloud --project PROJECT_ID kms keyrings create KEYRING_NAME --location global
ERROR: (gcloud.kms.keyrings.create) FAILED_PRECONDITION: Google Cloud KMS API has not been used in this project before, or it is disabled. Enable it by visiting https://console.developers.google.com/apis/api/cloudkms.googleapis.com/overview?project=... then retry. If you enabled this API recently, wait a few minutes for the action to propagate to our systems and retry.
```

Once that is completed, navigate to `kms > api-client` in the GCP Samples repository and create a `doit.sh` with the following content:
```
PROJECTID="PROJECT_ID"
LOCATION=global
KEYRING=KEYRING_NAME
CRYPTOKEY=CRYPTOKEY_NAME
echo '
consumer_key: ...
consumer_secret: ...
access_token: ...
access_token_secret: ...
' > /tmp/test_file
python snippets.py encrypt $PROJECTID $LOCATION $KEYRING $CRYPTOKEY \
  /tmp/test_file /tmp/test_file.encrypted 
python snippets.py decrypt $PROJECTID $LOCATION $KEYRING $CRYPTOKEY \
  /tmp/test_file.encrypted /tmp/test_file.decrypted
cat /tmp/test_file.decrypted
```

Fill in the `PROJECT_ID` from Google, the `KEYRING_NAME` you chose above, and take the `twitter` API Key and insert it in the place of `THE_SECRET`.

Before you run the script you need to set the environment variable `GOOGLE_APPLICATION_CREDENTIALS` to the path of `key.json` that you generated previously.

This will look something like:
```
export GOOGLE_APPLICATION_CREDENTIALS=FOO/BAR/BEZ/key.json
```

If you now run `bash doit.sh` it should print the API Key and the Encrypted version should be stored in `/tmp/test_file.encrypted`. In the below example I have renamed the file to `twitter.encrypted`.

2) Upload Secrets

Once you have both encrypted secret files we need to upload them to Google Cloud Storage for fetching in App Engine (and eventual decryption). Assuming the file is called `twitter.encrypted`, you would run something like the following:
```
$ gsutil mb -p PROJECT_ID gs://BUCKET_NAME
Creating gs://BUCKET_NAME/...

$ gsutil cp twitter.encrypted gs://BUCKET_NAME/

$ gsutil mv gs://BUCKET_NAME/twitter.encrypted gs://BUCKET_NAME/keys/twitter.encrypted

$ gsutil ls gs://BUCKET_NAME/keys
<BOTH FILES SHOULD BE LISTED HERE>
```

## Building

Initially, you will need to install the dependencies into a `lib` directory with the following command:
```
pip install -t lib -r requirements.txt
```

This `lib` directory is excluded from `git`.

## Local Development

The included `dev_appserver.py` loaded into your `PATH` is the best/easiest way to test before deployment ([doc](https://cloud.google.com/appengine/docs/standard/python/tools/using-local-server))

It can easily be launched with:
```
dev_appserver.py app.yaml
```

And then view `http://localhost:8000/cron` to run the `cron` locally. For this to work you will need to mock the KMS/GCS fetches otherwise you will get a 403 on the call to GCS bucket.

## Export Bucket

If you have `SAVE_TO_GCS` set to `True` it is expected that the `GCS_EXPORT_BUCKET` exists.

To create:
```
$ gsutil mb -p PROJECT_ID gs://GCS_EXPORT_BUCKET
```

## Deploying

This might be the easiest thing you own / operate as is the case with many things that are built to run on GCP.

Deploy:
```
$ gcloud --project PROJECT_ID app deploy
$ gcloud --project PROJECT_ID app deploy cron.yaml
```

On your first run if this is the first App Engine application you will be prompted to choose a region.

## Testing

No unit tests at this time.

Once deployed, you can hit the `/run` path on the URL.

## Logging

Google's Stackdriver service is sufficient for the logging needs of this service.

To view logs, you can use the `gcloud` CLI:
```
$ gcloud --project PROJECT_ID app logs read --service=default --limit 10
```

If you are not using the `default` project, you will need to change that parameter.

If you want to view the full content of the logs you can use the beta `logging` command:
```
$ gcloud beta logging read "resource.type=gae_app AND logName=projects/[PROJECT_ID]/logs/appengine.googleapis.com%2Frequest_log" --limit 10 --format json
```

Filling in the appropriate `[PROJECT_ID]` from GCP.

You can also see all available logs with the following command:
```
gcloud beta logging logs list
```

## Cost

The Twitter API for this usage has no cost associated.

Google Cloud Platform: The App Engine Standard Environment has three costs associated with it for this project.

1) Compute: Per-instance hour cost ([here](https://cloud.google.com/appengine/pricing#standard_instance_pricing)).
2) Network: Outgoing network traffic ([here](https://cloud.google.com/appengine/pricing#other-resources)).
3) Key Management Service: Key versions + Key use operations ([here](https://cloud.google.com/kms/#cloud-kms-pricing)).

Example Pricing:
Assumptions: We are running the job in Iowa, hourly, that takes < 1hr each run, network traffic is < 30MB, and we have one active CryptoKey w/one decryption requests for each run.

1) Compute: The B1 instance is $0.05/hr, we run 24x per day for a total of $1.20/day, (* 30) $36.00/month.
2) Network: We do not exceed 33MB per run and are charged the minimum of $0.12/month.
3) Key Management Service: One active key will be $0.06/month with the minimum of $0.03/month for Key use operations.

Estimated total under these conditions: $36.21/month.

Note: If you are utilizing the Free tier (https://cloud.google.com/free/) you get 28 Instance hours per day free on Google App Engine.  Since this job only takes a few minutes to run (at least under my account) it will not exceed the free limit and thus Compute costs $0: 15m/run * 24/hours per day = 360m/day = 6 Instance hours per day. Therefore the above estimate is $0.27/m.

## Limits

There are various limits with the Twitter API.

In the various API calls made by this service the following limit applies: **15 Requests per 15-min window**.

In addition lists can only contain 5,000 members and adding members to a list can only be done in batches of 100 members.

Due to these limits if you have > 15 * 100 (1.5k) friends, this will take > 1 run initially to complete due to the default rate limit.  After 4 runs it will be at the maximum allowed per list (5k).

## Known Issues

1. If you have more than 5,000 friends (accounts that you follow), a log message will be logged.  Only the first 5,000 friends returned will be synced.

2. The `/lists/list` endpoint should really be `lists/ownerships` but that is unimplemented by `tweepy` at this time which means if the former does not return the expected list in the first 100 results (90 subscribed lists, 10 owned lists), this may not work as expected.

3. Some exceptions are swallowed under `add_list_members` for example and will not raise an exception but will appear successful.

## Pull Requests

Sure, but please give me some time.

## License

Apache 2.0.
