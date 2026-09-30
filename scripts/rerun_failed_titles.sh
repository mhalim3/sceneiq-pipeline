#!/bin/zsh
cd /Users/mhalim/Downloads/sceneiq-pipeline
titles=(
  "White House Down (2013)"
  "Barbershop (2002)"
  "Boyz N the Hood (1991)"
  "La Bamba (1987)"
  "Driving Miss Daisy (1989)"
  "End of Watch (2012)"
  "Wrong Turn (2021)"
  "The DUFF (2015)"
  "Into the Blue (2005)"
  "Peeples (2013)"
  "3 From Hell (2019)"
  "21 Bridges (2019)"
  "Blacklight (2022)"
  "Barbershop 2: Back in Business (2004)"
  "Barbershop: The Next Cut (2016)"
  "Baby Boom (1987)"
  "16 Wishes (2010)"
  "Leon: The Professional (1994)"
  "Secret Window (2004)"
  "Spy Kids 4: All the Time in the World (2011)"
  "The Black Demon (2023)"
  "The Informer (2019)"
  "The Miracle Season (2018)"
  "The Spy Next Door (2010)"
  "Witchboard (2024)"
  "Zapped (2014)"
  "Disturbing the Peace (2020)"
)
i=0
for t in "${titles[@]}"; do
  slug=$(echo "$t" | tr 'A-Z ' 'a-z-' | tr -cd 'a-z0-9-')
  .venv/bin/python -m sceneiq "$t" --title-lookup --workers 3 > "/tmp/b4_${slug}.json" 2> "/tmp/b4_${slug}.log" &
  i=$((i+1))
  if (( i % 2 == 0 )); then wait; fi
done
.venv/bin/python -m sceneiq "Hercules (2014)" --workers 3 > /tmp/b4_hercules.json 2> /tmp/b4_hercules.log &
wait
echo "BATCH3B COMPLETE"
