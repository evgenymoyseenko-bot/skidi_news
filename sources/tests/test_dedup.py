from django.test import SimpleTestCase

from sources.parsing.dedup import find_duplicate


class FindDuplicateTests(SimpleTestCase):
    def test_finds_near_identical_title(self):
        recent = [(1, "На Роза Хутор открылся горнолыжный сезон 2026/2027")]
        duplicate_id, score = find_duplicate("В Роза Хутор открылся горнолыжный сезон 2026/2027", recent)
        self.assertEqual(duplicate_id, 1)
        self.assertIsNotNone(score)

    def test_unrelated_titles_are_not_duplicates(self):
        recent = [(1, "Цены на ски-пассы выросли на 10%")]
        duplicate_id, score = find_duplicate("Сборная России заняла первое место в слаломе", recent)
        self.assertIsNone(duplicate_id)
        self.assertIsNone(score)

    def test_picks_highest_scoring_match_among_several(self):
        recent = [
            (1, "Открытие сезона в Шерегеше 15 декабря"),
            (2, "В Шерегеше открылся горнолыжный сезон 15 декабря"),
        ]
        duplicate_id, score = find_duplicate("В Шерегеше открылся горнолыжный сезон 15 декабря", recent)
        self.assertEqual(duplicate_id, 2)
