#!/usr/bin/env bash
# Scaffold: a miniature CS_basics repo with one contest scratch file and one Hash_table neighbour.
set -euo pipefail
mkdir -p leetcode_python/Hash_table leetcode_python/lc_weekly/weekly_517 data

cat > README.md <<'MD'
# CS basics

## Hash Table

| # | Title | Solution | Time | Space | Difficulty | Note | Status |
|---|---|---|---|---|---|---|---|
| 1 | [Two Sum](https://leetcode.com/problems/two-sum/) | [Python](./leetcode_python/Hash_table/two-sum.py) | _O(n)_ | _O(n)_ | Easy | **hash table**, complement lookup | OK |
| 4007 | [Count Special Triplets](https://leetcode.com/problems/count-special-triplets/) | [Python](./leetcode_python/Hash_table/count-special-triplets.py) | _O(n)_ | _O(n)_ | Medium | **hash table**, prefix count, LC weekly | AGAIN(1) |

## Two Pointers

| # | Title | Solution | Time | Space | Difficulty | Note | Status |
|---|---|---|---|---|---|---|---|
| 167 | [Two Sum II](https://leetcode.com/problems/two-sum-ii-input-array-is-sorted/) | [Python](./leetcode_python/Two_Pointers/two-sum-ii.py) | _O(n)_ | _O(1)_ | Medium | **two pointers** | OK |
MD

cat > leetcode_python/Hash_table/two-sum.py <<'PY'
"""

1. Two Sum
Easy

Given an array of integers nums and an integer target, return indices of the two numbers such
that they add up to target.

Example 1:

Input: nums = [2,7,11,15], target = 9

Output: [0,1]

Constraints:

2 <= nums.length <= 10^4

"""

# V0
# IDEA : HASH MAP OF VALUE -> INDEX
#
#   For each x, the partner we need is target - x. If we have already seen it,
#   its index is in the map, so one pass is enough.
#
# time = O(n), space = O(n)
class Solution(object):
    def twoSum(self, nums, target):
        """
        :type nums: List[int]
        :type target: int
        :rtype: List[int]
        """
        seen = {}
        for i, x in enumerate(nums):
            if target - x in seen:
                return [seen[target - x], i]
            seen[x] = i
        return []
PY

cat > leetcode_python/lc_weekly/weekly_517/ws.txt <<'TXT'
# weekly 517

# LC 4038
# https://leetcode.com/problems/count-integers-appearing-in-a-single-block/
# Given nums, count the distinct values whose occurrences form one contiguous block.
# e.g. [1,1,2,2,3] -> 3 ; [1,2,1] -> 1 (only 2) ; [5] -> 1
# idea: for each value, last_idx - first_idx + 1 == count  <=> contiguous
class Solution:
    def countSpecialIntegers(self, nums):
        first, last, cnt = {}, {}, {}
        for i, x in enumerate(nums):
            if x not in first:
                first[x] = i
            last[x] = i
            cnt[x] = cnt.get(x, 0) + 1
        return sum(1 for x in cnt if last[x] - first[x] + 1 == cnt[x])

# LC 4039
# https://leetcode.com/problems/minimum-moves-to-balance-array/
# (not solved)
TXT

echo "2026-11-20 practiced LC 4007" > data/progress.txt

git init -q -b main
git -c user.name=eval -c user.email=eval@example.com add -A
git -c user.name=eval -c user.email=eval@example.com commit -qm "update 4007 py"
