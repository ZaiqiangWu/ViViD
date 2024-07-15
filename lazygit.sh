#!/bin/bash
if [ $# -eq 0 ]
then
    message="updated";
else
    message=$1;
fi
echo "commit with message: $message";

git add -u .
git commit -m "$message"
git push
