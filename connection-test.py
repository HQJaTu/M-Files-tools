#!/usr/bin/env python3

# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

import logging
import configargparse
import mfiles

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


def ping(server_address: str, user: str, password: str, vault: str) -> None:
    my_client = mfiles.MFilesClient(server=server_address,
                                    user=user,
                                    password=password,
                                    vault=vault)
    my_client.login(force_auth=True)
    log.debug("Server token: {}".format(my_client.server_token))
    log.debug("Vault token : {}".format(my_client.vault_token))
    status = my_client.get_server_status()
    log.info(status)  # {'Successful': True, 'Message': 'Test connection to server succeeded.'}

    capabilities = my_client.get_server_capabilities()
    log.info(
        capabilities
    )
    """
    Example capabilities response:
    {
        "ApplicationTaskQueueSupported": true,
        "AsyncModifyObjectClassSupported": true,
        "AsyncNACLChangePropagationSupported": true,
        "AsyncTasksRetrievalSupported": true,
        "AutomaticMetadataSupported": false,
        "ClientHttpApiSupported": true,
        "DetectTextLanguageSupported": true,
        "EmbeddingToSalesforceSupported": true,
        "EmbeddingToSharePointSupported": true,
        "ExternalRepositoryObjectMigrationSupported": true,
        "FacetSearchSupported": false,
        "FavorFullTextSearchSupported": true,
        "FileStreamingSupported": true,
        "FindDuplicatesSupported": true,
        "GetTextContentWithOptionsSupported": true,
        "HierarchicalObjectTypePropertiesSupported": true,
        "JSONExtApplicationsSupported": true,
        "LatestVersionPublicLinkSharingSupported": false,
        "NamedValueStorageConflictDetectionSupported": true,
        "ObjectDataRetrievalInChunksSupported": true,
        "ObjectPermissionsForClientSupported": true,
        "PropertyDefSearchabilitySupported": true,
        "PublicLinkSharingSupported": true,
        "RemoveFromRecentsSupported": true,
        "ReplicationConfigurationIDSupported": true,
        "ReverseGroupingLevelSupported": true,
        "SearchOptionsSupported": true,
        "SearchReferencedValueListItemsSupported": true,
        "SettingsManagerSupported": true,
        "UndeleteUserAccountByGUIDSupported": true,
        "UseUserVisibleACLInSearches": true,
        "VaultLoginAccountsSupported": true,
        "WebUserInterfaceSupported": true,
        "EmbeddingToGoogleGSuiteSupported": true,
        "WebClientsProductionTelemetryEnabled": false,
        "SharedLinkBasedWOPIOperationSupported": true,
        "MdccResolvedOnServer": true,
        "MFilesLinkSupported": true,
        "WebForceSessionBasedToken": false
    }
    """

    session = my_client.get_session_info()
    log.info(session)
    """
    Example session response:
    {
        "LicenseAllowsModifications": true,
        "HasFullControlOfVault": true,
        "IsAdminUser": false,
        "AccountName": "-redacted-",
        "LoginHint": "",
        "ACLMode": 1,
        "AuthenticationType": 3,
        "CanCreateobjects": true,
        "CanForceUndoCheckout": true,
        "CanManageCommonUISettings": true,
        "CanManageTraditionalFolders": true,
        "CanManageCommonViews": true,
        "CanMaterializeViews": true,
        "CanSeeAllObjects": true,
        "CanSeeDeletedObjects": true,
        "InternalUser": true,
        "UserID": 2,
        "isReadOnlyLicense": false,
        "ServerVaultCapabilities": {
            "VaultLoginAccountsSupported": true,
            "PublicLinkSharingSupported": true,
            "LatestVersionPublicLinkSharingSupported": false,
            "FacetSearchSupported": true,
            "PropertyDefSearchabilitySupported": true,
            "AutomaticMetadataSupported": false,
            "FileStreamingSupported": true,
            "SearchOptionsSupported": true,
            "JSONExtApplicationsSupported": true,
            "SearchReferencedValueListItemsSupported": true,
            "GetTextContentWithOptionsSupported": true,
            "DetectTextLanguageSupported": true,
            "ObjectDataRetrievalInChunksSupported": true,
            "SettingsManagerSupported": true,
            "ExternalRepositoryObjectMigrationSupported": true,
            "ClientHttpApiSupported": true,
            "ReverseGroupingLevelSupported": true,
            "ReplicationConfigurationIDSupported": true,
            "AsyncNACLChangePropagationSupported": true,
            "AsyncTasksRetrievalSupported": true,
            "FindDuplicatesSupported": true,
            "FavorFullTextSearchSupported": true,
            "ApplicationTaskQueueSupported": true,
            "ObjectPermissionsForClientSupported": true,
            "UndeleteUserAccountByGUIDSupported": true,
            "UseUserVisibleACLInSearches": true,
            "NamedValueStorageConflictDetectionSupported": true,
            "HierarchicalObjectTypePropertiesSupported": true,
            "EmbeddingToSharePointSupported": true,
            "EmbeddingToSalesforceSupported": true,
            "AsyncModifyObjectClassSupported": true,
            "WebUserInterfaceSupported": true,
            "RemoveFromRecentsSupported": true,
            "EmbeddingToGoogleGSuiteSupported": true,
            "FullControlVaultUserCanModifyCodeSupported": false,
            "EmbeddingToArcGisSupported": true,
            "WebClientsProductionTelemetryEnabled": false,
            "SharedLinkBasedWOPIOperationSupported": true,
            "MdccResolvedOnServer": true,
            "GreenLightsSupported": true,
            "NewWebWOPICoAuthoringSupported": true,
            "MFilesLinkSupported": true,
            "MdccConversionOnServer": true,
            "VaultLevelOptimizationJobsSupported": true,
            "ImportViewsWithOlderLastModifiedSupported": true,
            "VisitorLinksSupported": true,
            "AddObjectWithMultipleFilesSupported": true,
            "WebForceSessionBasedToken": false,
            "VersionEditorsPropertySupported": true,
            "UserAndGroupChangesSupported": true,
            "SetTierSupported": true
        },
        "SerialNumber": "-redacted-",
        "Deployment": "NewCloud",
        "licenseString": "Named",
        "licenseType": 1,
        "AutomaticMetadataEnabled": false,
        "CanDestroyObjects": true
    }
    """

    # object_types = my_client.objects()
    object_type = "eBook"
    ebook_info = my_client.get_info(object_type, category=mfiles.MFilesClient.CategoryType.OBJECT_TYPE)
    log.info(ebook_info)
    log.info(f"{object_type} has ID: {ebook_info['ID']}")
    """
    Example object type response:
    {
        "Name": "eBook",
        "NamePlural": "eBooks",
        "ID": 101,
        "CanHaveFiles": true,
        "HasOwner": false,
        "Owner": 0,
        "Hierarchial": false,
        "RealObjectType": true,
        "ShowInTaskPane": true,
        "objectTypeTargetsForBrowsing": [
            {
                "TargetObjectType": 9,
                "ViewCollection": 115
            }
        ],
        "AllowAdding": true,
        "DefaultPropertyDef": 1027,
        "OwnerPropertyDef": 1026,
        "External": false,
        "IsAddingAllowedForUser": true
    }
    """


def main():
    parser = configargparse.ArgParser(
        description='M-Files REST API Connection Test',
        config_file_parser_class=configargparse.TomlConfigParser(['m-files.tool']),
    )
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

    ping(args.rest_api_url, args.username, args.password, args.vault)


if __name__ == '__main__':
    main()
