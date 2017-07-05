from pathlib import Path
import logging
import time
import os
import sys
import csv
import praw
import sqlite3
import re
from bs4 import BeautifulSoup

PID_FILE = "AFSCbot.pid"
CRED_FILE = 'AFSCbotCreds.txt'
DB_FILE = "AFSCbotCommentRecord.db"
AFSC_FILE = 'AFSClist.csv'
LOG_TIME_FORMAT = "%Y/%m/%d %H:%M:%S "

# 'AFSCbot' must be changed to 'airforce' for a production version of the script
#SUBREDDIT = 'airforce+airnationalguard'
SUBREDDIT = 'AFSCbot'

# regex for locating AFSCs
ENLISTED_AFSC_REGEX = "([A-Z]?)(\d[A-Z]\d([013579]|X)\d)([A-Z]?)"
OFFICER_AFSC_REGEX = "([A-Z]?)(\d\d[A-Z](X?))([A-Z]?)"
ENLISTED_SKILL_LEVELS = ['Helper', '', 'Apprentice', '', 'Journeyman', '',
                       'Craftsman', '', 'Superintendent']

COMMENT_HEADER = ("^^You've ^^mentioned ^^an ^^AFSC, ^^here's ^^the"
                  " ^^associated ^^job ^^title:\n\n")


def main():

    # Initialize a logging object
    logging.basicConfig(filename='AFSCbot.log', level=logging.INFO)
    print_and_log("Starting script")

    # reddit user object
    reddit = login()

    # setup pid, database, and AFSC dicts
    open_pid()
    conn, dbCommentRecord = setup_database()

    #load all the AFSCs, prefixes and shreds into lists
    enlistedBaseAFSClist, enlistedBaseAFSCjt, officer_base_AFSC_list, \
    officer_base_AFSC_jt, enlistedPrefixList, enlistedPrefixTitle, \
    officer_prefix_list, officer_prefix_title, enlistedShredAFSC, \
    enlistedShredList, enlistedShredTitle, officer_shred_AFSC, \
    officer_shred_list, officer_shred_title = get_AFSCs()

    AFSC_links = get_AFSC_links(reddit)

    # subreddit instance of /r/AirForce.
    rAirForce = reddit.subreddit(SUBREDDIT)

    print_and_log("Starting processing loop for subreddit: " + SUBREDDIT)
    comments_seen = 0
    try:
        while True:
            try:
                # stream all comments from /r/AirForce
                for rAirForceComment in rAirForce.stream.comments():
                    process_comment(rAirForceComment, conn,
                         dbCommentRecord, enlistedBaseAFSClist,
                         enlistedBaseAFSCjt, officer_base_AFSC_list,
                         officer_base_AFSC_jt, enlistedPrefixList,
                         enlistedPrefixTitle,
                         officer_prefix_list, officer_prefix_title,
                         enlistedShredAFSC,
                         enlistedShredList, enlistedShredTitle,
                         officer_shred_AFSC,
                         officer_shred_list, officer_shred_title)
                    comments_seen += 1
                    print()
                    print("Comments processed since start of script: {}".format(
                            comments_seen))

            # what to do if Ctrl-C is pressed while script is running
            except KeyboardInterrupt:
                print_and_log("Exiting due to keyboard interrupt",
                              error=True)
                sys.exit(0)

            except KeyError:
                print_and_log("AFSC not found in the dictionary, but "
                              "the dict was called for some reason",
                              error=True)

            # is this necessary? shouldn't program just crash
            # otherwise we can cover specific errors related to connection
            # makes debugging harder without printing full exception info
            #except Exception as err:
            #    print_and_log("Unhandled Exception: " + str(err),
            #                  error=True)

            finally:
                conn.commit()
    finally:
        conn.commit()
        conn.close()
        os.unlink(PID_FILE)


def open_pid():
    # Get the PID of this process
    # Exit if a version of the script is already running
    if os.path.isfile(PID_FILE):
        print_and_log("PID already open, exiting script", error=True)
        sys.exit(1)
    else:
        # Create the lock file for the script
        pid = str(os.getpid())
        open(PID_FILE, 'w').write(pid)


def login():
    try:
        creds = open(CRED_FILE, 'r')
        print_and_log("Opened creds file")
    except OSError:
        print_and_log("Couldn't open {}".format(CRED_FILE), error=True)
        sys.exit(1)

    agent = creds.readline().strip()
    ID = creds.readline().strip()
    secret = creds.readline().strip()
    client_user = creds.readline().strip()
    client_password = creds.readline().strip()
    creds.close()

    # Try to login or sleep/wait until logged in, or exit if user/pass wrong
    NotLoggedIn = True
    while NotLoggedIn:
        try:
            reddit = praw.Reddit(
                user_agent=agent,
                client_id=ID,
                client_secret=secret,
                username=client_user,
                password=client_password)
            print_and_log("Logged in")
            NotLoggedIn = False
        except praw.errors.InvalidUserPass:
            print_and_log("Wrong username or password", error=True)
            exit(1)
        except Exception as err:
            print_and_log(str(err), error=True)
            time.sleep(5)
    return reddit


def setup_database():
    dbFile = Path(DB_FILE)

    # check to see if database file exists
    if dbFile.is_file():
        # connection to database file
        conn = sqlite3.connect(DB_FILE)
        # database cursor object
        dbCommentRecord = conn.cursor()

    else:  # if it doesn't, create it
        conn = sqlite3.connect(DB_FILE)
        dbCommentRecord = conn.cursor()
        dbCommentRecord.execute('''CREATE TABLE comments(comment text)''')
    return conn, dbCommentRecord


def get_AFSCs():
    # List for holding all AFSCs and job titles
    enlistedBaseAFSClist = []
    enlistedBaseAFSCjt = []
    officer_base_AFSC_list = []
    officer_base_AFSC_jt = []
    enlistedPrefixList = []
    enlistedPrefixTitle = []
    officer_prefix_list = []
    officer_prefix_title = []
    enlistedShredAFSC = []
    enlistedShredList = []
    enlistedShredTitle = []
    officer_shred_AFSC = []
    officer_shred_list = []
    officer_shred_title = []

    # Load the AFSCs, prefixes and shreds into lists, then use the AFSC as the key to the Job Title, shreds, etc...
    with open('EnlistedAFSCs.csv', newline='') as f:
        reader = csv.reader(f, delimiter='#')
        for row in reader:
            enlistedBaseAFSClist.append(row[0])
            enlistedBaseAFSCjt.append(row[1])

    with open('OfficerAFSCs.csv', newline='') as f:
        reader = csv.reader(f, delimiter='#')
        for row in reader:
            officer_base_AFSC_list.append(row[0])
            officer_base_AFSC_jt.append(row[1])

    with open('EnlistedPrefixes.csv', newline='') as f:
        reader = csv.reader(f, delimiter=',')
        for row in reader:
            enlistedPrefixList.append(row[0])
            enlistedPrefixTitle.append(row[1])

    with open('OfficerPrefixes.csv', newline='') as f:
        reader = csv.reader(f, delimiter=',')
        for row in reader:
            officer_prefix_list.append(row[0])
            officer_prefix_title.append(row[1])

    with open('OfficerShreds.csv', newline='') as f:
        reader = csv.reader(f, delimiter=',')
        for row in reader:
            officer_shred_AFSC.append(row[0])
            officer_shred_list.append(row[1])
            officer_shred_title.append(row[2])

    with open('EnlistedShreds.csv', newline='') as f:
        reader = csv.reader(f, delimiter=',')
        for row in reader:
            enlistedShredAFSC.append(row[0])
            enlistedShredList.append(row[1])
            enlistedShredTitle.append(row[2])

    '''Easier way to pass the arguments?
    AFSCdict = {'enlistedBaseAFSClist' : enlistedBaseAFSClist,
                'enlistedBaseAFSCjt' : enlistedBaseAFSCjt,
                'officer_base_AFSC_list' : officer_base_AFSC_list,
                'officer_base_AFSC_jt' : officer_base_AFSC_jt,
                'enlistedPrefixList' : enlistedPrefixList,
                'enlistedPrefixTitle' : enlistedPrefixTitle,
                'officer_prefix_list' : officer_prefix_list,
                'officer_prefix_title' : officer_prefix_title,
                'enlistedShredAFSC' : enlistedShredAFSC,
                'enlistedShredList' : enlistedShredList,
                'enlistedShredTitle' : enlistedShredTitle,
                'officer_shred_AFSC' : officer_shred_AFSC,
                'officer_shred_list' : officer_shred_list,
                'officer_shred_title' : officer_shred_title}

    if AFSCdict == {}:
        print("AFSC dict empty: exiting program")
        sys.exit(1)
'''

    return enlistedBaseAFSClist, enlistedBaseAFSCjt, officer_base_AFSC_list, \
            officer_base_AFSC_jt, enlistedPrefixList, enlistedPrefixTitle, \
            officer_prefix_list, officer_prefix_title, enlistedShredAFSC, \
            enlistedShredList, enlistedShredTitle, officer_shred_AFSC, \
            officer_shred_list, officer_shred_title


def process_comment(rAirForceComment, conn,
                     dbCommentRecord, enlistedBaseAFSClist,
                     enlistedBaseAFSCjt, officer_base_AFSC_list,
                     officer_base_AFSC_jt, enlistedPrefixList,
                     enlistedPrefixTitle,
                     officer_prefix_list, officer_prefix_title,
                     enlistedShredAFSC,
                     enlistedShredList, enlistedShredTitle,
                     officer_shred_AFSC,
                     officer_shred_list, officer_shred_title):

    # prints a link to the comment. A True for permalink
    # generates a fast find (but is not an accurate link,
    # just makes the script faster (SIGNIFICANTLY FASTER)
    permlink = "http://www.reddit.com{}/".format(
                rAirForceComment.permalink(True))

    print_and_log("Processing comment: " + permlink)

    # Pulls all comments previously commented on
    dbCommentRecord.execute("SELECT * FROM comments WHERE comment=?",
                            (rAirForceComment.id,))

    id_exists = dbCommentRecord.fetchone()

    # Make sure we don't reply to the same comment twice
    # or to the bot itself
    if id_exists:
        print("Already processed comment: {}, skipping".format(
                rAirForceComment.id))
    elif rAirForceComment.author == "AFSCbot":
        print("Author was the bot, skipping...")
    else:
        formattedComment = rAirForceComment.body.upper()

        # Search through the comments for things that look like enlisted AFSCs
        enlisted_AFSC_search = re.compile(ENLISTED_AFSC_REGEX, re.IGNORECASE)
        matched_comments_enlisted = enlisted_AFSC_search.finditer(
            formattedComment)

        officer_AFSC_search = re.compile(OFFICER_AFSC_REGEX,
                                         re.IGNORECASE)

        matched_comments_officer = officer_AFSC_search.finditer(
            formattedComment)

        # Keep a list of matched AFSCs so they're only posted once
        matchList = []
        commentText = ""

        for enlisted_individual_matches in matched_comments_enlisted:
            commentText = process_enlisted(commentText, enlisted_individual_matches,
                                           matchList, enlistedBaseAFSClist,
                                           enlistedBaseAFSCjt, enlistedPrefixList,
                                           enlistedPrefixTitle, enlistedShredAFSC,
                                           enlistedShredList, enlistedShredTitle)

        for officer_individual_matches in matched_comments_officer:
            commentText = process_officer(commentText, officer_individual_matches,
                                          matchList, officer_base_AFSC_list,
                                          officer_prefix_list, officer_prefix_title,
                                          officer_base_AFSC_jt, officer_shred_list,
                                          officer_shred_AFSC, officer_shred_title)

        # if commentText is not empty, build reply
        if commentText != "":
            comment_info_text = ("Commenting on AFSC: {} by:"
                                 " {}. Comment ID: {}".format(
                                matchList, rAirForceComment.author,
                                rAirForceComment.id))
            print_and_log(comment_info_text)


            rAirForceComment.reply(COMMENT_HEADER + commentText)

            dbCommentRecord.execute('INSERT INTO comments VALUES (?);',
                                    (rAirForceComment.id,))
            conn.commit()


def process_enlisted(commentText, enlisted_individual_matches, matchList,
                     enlistedBaseAFSClist, enlistedBaseAFSCjt, enlistedPrefixList,
                     enlistedPrefixTitle, enlistedShredAFSC, enlistedShredList,
                     enlistedShredTitle):

    whole_match = enlisted_individual_matches.group(0)
    prefix = enlisted_individual_matches.group(1)
    afsc = enlisted_individual_matches.group(2)
    skill_level = enlisted_individual_matches.group(3)
    suffix = enlisted_individual_matches.group(4)

    if whole_match in matchList:
        return commentText
    else:
        #replaces the skill level with an X
        tempAFSC = list(afsc)
        tempAFSC[3] = 'X'
        tempAFSC = "".join(tempAFSC)

        for i in range(len(enlistedBaseAFSClist)):
            #did we get a match on the base AFSC?
            if tempAFSC in enlistedBaseAFSClist[i]:
                matchList.append(whole_match)
                commentText += whole_match + " = "

                #Is there a prefix? If so, add it
                if prefix:
                    for j in range(len(enlistedPrefixList)):
                        if prefix == enlistedPrefixList[j]:
                            commentText += enlistedPrefixTitle[j] + " "
                commentText += enlistedBaseAFSCjt[i]

                if skill_level != 'X' and skill_level != '0':
                    commentText += " " + \
                    ENLISTED_SKILL_LEVELS[int(skill_level) - 1]

                # Is there a suffix? If so, add it
                if suffix:
                    for j in range(len(enlistedShredList)):
                        if (tempAFSC == enlistedShredAFSC[j]
                                and suffix == enlistedShredList[j]):
                            print(enlistedShredTitle[j])
                            commentText += ", " + enlistedShredTitle[j]

                commentText += "\n\n"
    return commentText


def process_officer(commentText, officer_individual_matches, matchList,
                    officer_base_AFSC_list, officer_prefix_list,
                    officer_prefix_title, officer_base_AFSC_jt,
                    officer_shred_list, officer_shred_AFSC, officer_shred_title):

    whole_match = officer_individual_matches.group(0)
    prefix = officer_individual_matches.group(1)
    afsc = officer_individual_matches.group(2)
    skill_level = officer_individual_matches.group(3)
    suffix = officer_individual_matches.group(4)

    print("Whole match: " + whole_match)
    if prefix:
        print("Prefix: " + prefix)
    print("AFSC: " + afsc)
    if skill_level:
        print("Skill Level: " + skill_level)
    if suffix:
        print("Suffix: " + suffix)

    if whole_match in matchList:
        return commentText
    else:
        tempAFSC = afsc
        print("Pre temp: " + tempAFSC)
        if not skill_level:
            tempAFSC = tempAFSC + 'X'
            print("Post temp: " + tempAFSC)

        for i in range(len(officer_base_AFSC_list)):
            if tempAFSC in officer_base_AFSC_list[i]:
                matchList.append(whole_match)
                commentText += whole_match + " = "

                if prefix:
                    for j in range(len(officer_prefix_list)):
                        if prefix == officer_prefix_list[j]:
                            commentText += officer_prefix_title[j] + " "
                commentText += officer_base_AFSC_jt[i]

                if suffix:
                    for j in range(len(officer_shred_list)):
                        if (tempAFSC == officer_shred_AFSC[j]
                                and suffix == officer_shred_list[j]):
                            print(officer_shred_title[j])
                            commentText += ", " + officer_shred_title[j]

                commentText += "\n\n"
    return commentText


def get_AFSC_links(reddit):
    # gets dict of AFSC to link on /r/AirForce wiki
    wiki_page = reddit.subreddit("AirForce").wiki["index"]
    wiki_soup = BeautifulSoup(wiki_page.content_html, "html.parser")
    links = wiki_soup.find_all("a")

    AFSC_links = {}
    for link in links:
        # not all links have /r/AirForce/wiki/jobs so this is more generalized
        # using only /r/AirForce/wiki/
        if "www.reddit.com/r/AirForce/wiki/" in link["href"]:
            AFSC_code = link["href"].split("/")[-1]
            AFSC_links[AFSC_code] = link["href"]
    return AFSC_links


def print_and_log(text, error=False):
    print(text)
    if error:
        logging.error(time.strftime(LOG_TIME_FORMAT) + text)
    else:
        logging.info(time.strftime(LOG_TIME_FORMAT) + text)


if __name__ == "__main__":
    main()
