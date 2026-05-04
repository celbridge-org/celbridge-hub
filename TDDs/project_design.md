Version 2
=========

- this is a major feature upgrade
    - up to now, this site allow upload and downloads of ZIPs
        - and published the contents of the ZIPs for public access via the web
  - this new version works with uploaded 'packages' and manages their history and versioning
    - there are 3 types of package: [package] 'type'
      - "mod", "project", or "app"
      - this type can be found in the  `package.toml` file inside the ZIP
        - if a package tries to be uploaded that does NOT contain a  `package.toml` file
          - then reject the upload, and return an error message "invalid package - missing `package.toml` file"
        - if a package tries to be uploaded that does NOT contain  [package] 'type' in its  `package.toml` file
          - then reject the upload, and return an error message "invalid package - missing `[package] 'type' property` in package.toml` file"
        - if a package tries to be uploaded that does NOT contain  [package] 'author' in its  `package.toml` file
            - then reject the upload, and return an error message "invalid package - missing `[package] 'author' property` in package.toml` file"
        - if a package tries to be uploaded whose [package] 'type' is not one of "mod", "project", or "app"
          - then reject the upload, and return an error message "invalid package - `[package] 'type'` property in `package.toml` file must be one of "mod", "project", or "app""

[] use unique package name from TOML file to track versions of a package
    - here is an example `package.toml` file for a package named "tiptap-notes":
        ```toml
        [package]
        name    = "tiptap-notes"
        type    = "mod"            # required: "mod", "project", or "app"
        author  = "celbridge"
        license = "MIT"
        tags    = ["editor", "notes", "rich-text"]
        ```


-[] add a DB table for 'author'
- integer: id (primary key)
- string: name

-[] add a DB table for 'package_type'
- integer: id (primary key)
- string: name (have 3 rows, for "mod", "project", or "app")

-[] add a DB table for 'package'
    - integer: id (primary key)
    - string: name (unique)
    - integer: package_type_id (foreign key)

-[] add a DB table for 'package_version'
    - this will be the 'source of truth' about the version history for packages
        - integer: id (primary key)
        - integer: version (this starts at 1 for a new package, and increments by 1 for each new uploaded version)
        - integer: author_id (foreign key) - this is based on the ID of the author whose name matches that extracted from the `[package] 'author'` property of the `package.toml` file - if no match then create a new author record and reference its ID here
        - string: date (stored in UTC format, e..g UTC2026-04-29T15:41:32Z) - this is set by this project to the current datetime when a new package, or package version is successfully uploaded
        - string: summary - if a summary message was included with the ZIP file, then stored the message here for the new version
    

-[] steps to follow to manange version history of packages
    -[] step 1
        - unzip uploaded package
    -[] step 2
        - get the 'name' property from the package
    -[] step 3
        - look 


-[] when a new package / new version of a package is uploaded, then a history file is to be created in the following format, using the data from the 'package_version' table
    -[] this history file is to be added to the package contents (replacing any 'history.md' that was uploaded), and a new ZIP created containing this history file
    - (so when an API client downloads this package version, it will contain this generated 'history.md' file)


- here is an example is for a package named 'piskel-editor'
    - it describes the history of the package, take from the DB tables of this system, listing the history in most-recent-first to oldest-last sequence
    - this example shows that this 'piskel-editor' v1 was based on a different package, 'matt-editor v10'       
        ```markdown
        # piskel-editor v2
        
        - author: chris
        - date: UTC2026-04-29T15:41:32Z
        
        Added a rectangle tool
        
        # piskel-editor v1
        
        - author: chris
        - date: UTC2026-04-28T15:41:32Z
        
        Added a circle tool
        
        # matt-editor v10
        
        - author: matt
        - date: UTC2025-04-28T15:41:32Z
        
        Added a pencil tool
        ```

-[] add 2 new end points, for the 'forking' of an existing package version to a new package
    - the first forks from the latest version of the package ID
      - POST /api/upload/fork-pacakge/<int:packageID>
  - the second forks from the named version of the package ID
      - POST /api/upload/fork-package-version/<int:versionID>
    

