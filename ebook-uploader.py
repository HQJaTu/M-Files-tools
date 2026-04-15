#!/usr/bin/env python3

# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

import hashlib
import logging
import os
import pathlib
import pickle
import re
from http import HTTPStatus
from typing import Optional

import configargparse
import mfiles
import truststore
from lxml import etree
from tika import parser

log = logging.getLogger(__name__)


def _setup_logger(options: configargparse.Namespace) -> None:
    log_level: int = logging.getLevelName(options.log_level)
    if not log_level:
        raise ValueError("Unkown logging level '{}'!".format(options.log_level))

    logging.basicConfig(
        format="%(asctime)s [%(threadName)-12.12s] [%(levelname)-5.5s]  [%(name)s] %(message)s",
        level=log_level
    )
    if log_level <= logging.DEBUG:
        # This guy is noisy! Use a muffler.
        urllib3_log = logging.getLogger('urllib3')
        urllib3_log.setLevel(logging.INFO)
        keyring_log = logging.getLogger('keyring')
        keyring_log.setLevel(logging.INFO)
        win32ctypes_log = logging.getLogger('win32ctypes')
        win32ctypes_log.setLevel(logging.INFO)


def calculate_sha1(file_path: str) -> str:
    hash_algo = hashlib.sha1()

    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hash_algo.update(chunk)

    return hash_algo.hexdigest()


def is_pdf_by_sig(file_path: str) -> bool:
    """
    Helper:
    Determine if a given filename is a PDF file or not
    :param file_path: File to check for
    :return: True if signature suggests a PDF-file
    """
    try:
        with open(file_path, 'rb') as f:
            # Read the first 5 bytes
            header = f.read(5)
            return header == b'%PDF-'
    except IOError:
        pass

    return False


def parse_files(filename_or_dir: str, tika_server_url: str, storage_directory: str) -> int:
    """
    Worker: Wrap
    :param filename_or_dir:
    :param tika_server_url:
    :param storage_directory:
    :return:
    """
    file_count = 0
    ebook_count = 0
    if os.path.isdir(filename_or_dir):
        for root, dirs, files in os.walk(filename_or_dir):
            log.info("Scanning directory: {}".format(root))

            # Process the files
            for file in files:
                file_count += 1
                full_path = os.path.join(root, file)
                if is_pdf_by_sig(full_path):
                    # log.info("Parsing PDF file: {}".format(full_path))
                    result = parse_file(full_path, tika_server_url, storage_directory)
                    if 'xhtml' in result:
                        ebook_count += 1
    elif os.path.isfile(filename_or_dir):
        log.info("Scanning file: {}".format(filename_or_dir))
        file_count = 1
        result = parse_file(filename_or_dir, tika_server_url, storage_directory)
        if 'xhtml' in result:
            ebook_count += 1

    log.info("Did a total of {} files, {} e-books".format(file_count, ebook_count))

    return file_count


def should_rebuild(source_path: str, cache_path: str) -> bool:
    """
    Helper: Determine if a file needs to be re-Tika'd
    :param source_path: PDF-file
    :param cache_path: Cache file
    :return: True if cache file needs to be done
    """
    source = pathlib.Path(source_path)
    cache = pathlib.Path(cache_path)

    # 1. Check if cache exists
    if not cache.exists():
        return True

    # 2. Compare modification timestamps
    # .stat().st_mtime returns a float representing seconds since epoch
    return source.stat().st_mtime > cache.stat().st_mtime


def parse_file(filename: str, tika_server_url: str, storage_directory: str) -> dict:
    """
    Worker:
    :param filename:
    :param tika_server_url:
    :param storage_directory:
    :return:
    """
    log.info("Parsing file {}".format(filename))
    sha1 = calculate_sha1(filename)
    parsed_filename = os.path.join(storage_directory, f"{sha1}.bin")
    if not should_rebuild(filename, parsed_filename):
        # Return something we processed earlier
        log.debug("Returning file {} data from cache: {}".format(filename, parsed_filename))
        with open(parsed_filename, 'rb') as f:
            data = pickle.load(f)
            return data

    # Go Tika the file
    log.debug("Calling Tika to parse the file: {}".format(filename))
    try:
        parsed = parser.from_file(
            filename,
            serverEndpoint=tika_server_url,
            xmlContent=True,
            requestOptions={
                "verify": True,
            }
        )
    except Exception as e:
        log.error("Error parsing file: {}".format(e))
        return {}

    if not parsed or 'status' not in parsed:
        raise ValueError("Internal error. Error parsing file {}".format(filename))
    if parsed['status'] != HTTPStatus.OK:
        raise ValueError("Error parsing file {}. HTTP/{}".format(filename, parsed['status']))
    content_len = len(parsed['content'])
    log.debug("Parsed file {} (SHA-1: {}). Got {} bytes of content".format(filename, sha1, content_len))

    """
    Metadata example:
    {
        "Content-Length": "2027797",
        "Content-Type": "application/pdf",
        "dc:format": "application/pdf; version=1.4",
        "dc:language": "en-US",
        "dcterms:created": "2022-08-19T16:52:28Z",
        "dcterms:modified": "2022-08-19T16:52:34Z",
        "xmp:CreatorTool": "Adobe InDesign 17.3 (Windows)",
        "xmp:CreateDate": "2022-08-19T16:52:28Z",
        "xmp:ModifyDate": "2022-08-19T16:52:34Z",
        "xmp:MetadataDate": "2022-08-19T16:52:34Z",
        "xmp:pdf:Producer": "Adobe PDF Library 16.0.7",
        "xmpMM:DerivedFrom:DocumentID": "xmp.did:1270208c-fccc-5b4b-bd15-39be7c629c17",
        "xmpMM:DocumentID": "xmp.id:7b75c61b-1aeb-8c4b-924b-356cd5eb0f44",
        "xmpMM:DerivedFrom:InstanceID": "xmp.iid:a70f575c-8b93-d04d-a27e-d0174ce861c9",
        "xmpMM:InstanceID": "uuid:3546a22d-683f-4dbd-a045-9e23132c33b3",
        "xmpTPg:NPages": "56",
        "resourceName": "b'8-Data-Modeling-Patterns-in-Redis.pdf'",
        "access_permission:fill_in_form": "true",
        "access_permission:can_print_faithful": "true",
        "access_permission:extract_for_accessibility": "true",
        "access_permission:modify_annotations": "true",
        "access_permission:extract_content": "true",
        "access_permission:can_print": "true",
        "access_permission:assemble_document": "true",
        "access_permission:can_modify": "true",
        "pdf:PDFVersion": "1.4",
        "pdf:hasXFA": "false",
        "pdf:num3DAnnotations": "0",
        "pdf:docinfo:creator_tool": "Adobe InDesign 17.3 (Windows)",
        "pdf:hasCollection": "false",
        "pdf:encrypted": "false",
        "pdf:containsNonEmbeddedFont": "false",
        "pdf:hasMarkedContent": "true",
        "pdf:ocrPageCount": "0",
        "pdf:annotationTypes": "null",
        "pdf:docinfo:producer": "Adobe PDF Library 16.0.7",
        "pdf:annotationSubtypes": "Link",
        "pdf:containsDamagedFont": "false",
        "pdf:unmappedUnicodeCharsPerPage": [
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0",
            "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0"
        ],
        "pdf:overallPercentageUnmappedUnicodeChars": "0.0",
        "pdf:docinfo:modified": "2022-08-19T16:52:34Z",
        "pdf:producer": "Adobe PDF Library 16.0.7",
        "pdf:totalUnmappedUnicodeChars": "0",
        "pdf:hasXMP": "true",
        "pdf:charsPerPage": [
            "151", "1833", "2151", "1660", "1560", "637", "986", "1212", "786", "796", "485", "715", "1651",
            "1100", "1537", "640", "282", "759", "1648", "742", "1614", "510", "805", "1444", "1506", "1294",
            "651", "689", "367", "888", "1601", "566", "560", "1098", "999", "932", "2024", "1555", "620", "1173", "1031",
            "1224", "524", "1505", "973", "889", "673", "1857", "1102", "1079", "1533", "1284", "254", "1975", "1902","736"
        ],
        "pdf:docinfo:trapped": "False",
        "pdf:docinfo:created": "2022-08-19T16:52:28Z",
        "X-TIKA:parse_time_millis": "535",
        "X-TIKA:Parsed-By-Full-Set": [
            "org.apache.tika.parser.DefaultParser",
            "org.apache.tika.parser.pdf.PDFParser"
        ],
        "X-TIKA:content_handler": "ToTextContentHandler",
        "X-TIKA:Parsed-By": [
            "org.apache.tika.parser.DefaultParser",
            "org.apache.tika.parser.pdf.PDFParser"
        ],
        "X-TIKA:embedded_depth": "0",
    }
    """

    xhtml = extract_primary_document(parsed['content'])
    del parsed['content']
    if not xhtml:
        log.warning("Tika failed to extract XHTML content from {}".format(parsed_filename))
        return parsed

    # Looking good!
    parsed['xhtml'] = xhtml

    if False:
        content_filename = os.path.join(storage_directory, f"{sha1}.bin")
        with open(content_filename, "w", encoding="utf-8") as f:
            f.write(xhtml)
        log.info("Wrote content into {}".format(content_filename))

    with open(parsed_filename, 'wb') as handle:
        pickle.dump(parsed, handle, protocol=pickle.HIGHEST_PROTOCOL)

    return parsed


def extract_primary_document(xhtml_string: str) -> Optional[str]:
    """
    Helper function to extract primary document from xhtml string
    :param xhtml_string: XHTML string as returned by Apache Tika
    :return: XHTML string that is actually valid XML
    """

    # Split by the XHTML namespace declaration or the opening <html> tag
    # This keeps the first occurrence and discards everything after the second <html> starts
    parts = re.split(r'(?=<html)', xhtml_string, flags=re.IGNORECASE)

    parser = etree.XMLParser(recover=True, remove_comments=True)
    for part in parts:
        if not part:
            continue
        root = etree.fromstring(part.encode('utf-8'), parser=parser)
        is_xhtml = 'http://www.w3.org/1999/xhtml' in root.nsmap.values()
        if is_xhtml:
            return part

    return None


def upload(server_address: str, user: str, password: str, vault: str) -> None:
    # API docs:
    # https://developer.m-files.com/APIs/REST-API/
    # Community:
    # https://community.m-files.com/forums-1552881334/f/m-files-api
    my_client = mfiles.MFilesClient(server=server_address,
                                    user=user,
                                    password=password,
                                    vault=vault)

    print(my_client)
    # log.


def main():
    parser = configargparse.ArgParser(
        description='M-Files Uploader',
        config_file_parser_class=configargparse.TomlConfigParser(
            ['m-files.tool.ebook-uploader', 'm-files.tool.common']
        ),
    )
    parser.add_argument('ebook',
                        metavar='EBOOK-FILENAME',
                        help='eBook filename to parse and upload')
    parser.add_argument('--rest-api-url',
                        required=True,
                        help="M-Files Vault REST API URL endpoint")
    parser.add_argument('--username',
                        required=True,
                        help="M-Files Vault username")
    parser.add_argument('--password',
                        required=True,
                        help="M-Files Vault password")
    parser.add_argument('--vault',
                        help="M-Files Vault GUID. If none given, will default to first vault user has access to.")
    parser.add_argument('--tika-server-url',
                        required=True,
                        help="Tika server URL endpoint")
    parser.add_argument('--storage-directory',
                        required=True,
                        help="Directory to store uploaded file metadata")
    parser.add_argument('--log-level',
                        default='WARNING',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        env_var='LOG_LEVEL',
                        help="Python logger log level. Default: WARNING")
    parser.add_argument('-c', '--config',
                        is_config_file=True,
                        help='Config file path')
    args = parser.parse_args()
    _setup_logger(args)
    truststore.inject_into_ssl()

    parse_files(args.ebook, args.tika_server_url, args.storage_directory)
    upload(args.rest_api_url, args.username, args.password, args.vault)


if __name__ == '__main__':
    main()
