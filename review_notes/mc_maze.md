---
dandisets: ["000070", "000128", "000138", "000139", "000140"]
---

# MC_Maze

MC_Maze ([000128](https://dandiarchive.org/dandiset/000128)) and its Large,
Medium and Small variants ([000138](https://dandiarchive.org/dandiset/000138),
[000139](https://dandiarchive.org/dandiset/000139),
[000140](https://dandiarchive.org/dandiset/000140)) share primary papers, and
papers reanalysing them cite those papers or the Neural Latents Benchmark (NLB)
rather than an identifier. The citation does not say which dandiset was used,
but the rest of the paper usually does. Look for:

* **Counts.** Each variant is a different recording session, so a reported
  neuron, condition or trial count picks one out. These are from the NLB paper
  ([Pei et al. 2021](https://arxiv.org/abs/2109.04463), Table 4).

  | Dandiset | Variant | Neurons | Held-in | Held-out | Conditions | Training trials | Test trials |
  |---|---|---|---|---|---|---|---|
  | 000128 | MC_Maze | 182 | 137 | 45 | 108 | 2295 | 574 |
  | 000138 | MC_Maze_Large | 162 | 122 | 40 | 27 | 500 | 100 |
  | 000139 | MC_Maze_Medium | 152 | 114 | 38 | 27 | 250 | 100 |
  | 000140 | MC_Maze_Small | 142 | 107 | 35 | 27 | 100 | 100 |

  Table 4 lists the held-in and held-out units separately and gives no total.
  **Neurons** is the two added together, verified independently of the paper by
  counting the neurons in the sole training file each of these dandisets holds.

* **The NLB leaderboard.** A paper that reports NLB results was usually
  submitted to the [leaderboard](https://eval.ai/web/challenges/challenge-page/1256/leaderboard).
  The "MC_Maze" leaderboards score 000128 only. The variants are scored only on
  the "MC_Maze Scaling" leaderboards, in columns headed [500], [250] and [100].
  A method listed on MC_Maze and on no MC_Maze Scaling leaderboard used 000128.
  The leaderboards list public submissions only.
* **Code and data availability.** Dataset names such as `mc_maze_large`, a
  dandiset URL, or a download script in the paper's repository.

Where the evidence identifies one dandiset, call that pair **Reuse** and its
siblings on the same paper **Neither**, and put the evidence in the note on
each, so whoever reads any one of them sees why. **Ambiguous Reuse** is for when
none of this settles it: the data was reused, and nothing in the paper, its code
or the leaderboard says which dandiset it came from. Say in the note what you
checked.

One more dandiset shares one of these papers. Churchland et al. 2012
([10.1038/nature11129](https://doi.org/10.1038/nature11129)) is named by the
four MC_Maze dandisets and also by
[000070](https://dandiarchive.org/dandiset/000070), Neural population dynamics
during reaching. Churchland et al. 2010
([10.1016/j.neuron.2010.09.015](https://doi.org/10.1016/j.neuron.2010.09.015))
is named by the MC_Maze dandisets only. The counts and leaderboard above pick
out one MC_Maze dandiset but do not tell 000070 apart from them.
