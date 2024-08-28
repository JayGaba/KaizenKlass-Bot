import os
import requests
import discord
from discord import app_commands
from discord.ui import Button, View, Select
from dotenv import load_dotenv
import aiosqlite
from discord import ButtonStyle

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# dict to keep track of active views
view_dict = {}

DATABASE_NAME = "user_preferences.db"


def fetch_subjects():
    response = requests.get("https://api.kaizenklass.me/api/v1/get-subjects")
    response.raise_for_status()  
    subjects = response.json().get("subjects", [])
    return subjects

# paginated dropdown menu for registered subjects + subject registration
class PaginatedSelect(Select):
    def __init__(self, subjects, page, user_id):
        self.subjects = subjects
        self.page = page
        self.user_id = user_id
        options_per_page = 25
        start = page * options_per_page
        end = start + options_per_page
        options = subjects[start:end]

        self.subject_names = {subject["subject_uuid"]: subject["subject"] for subject in options}


        select_options = [
            discord.SelectOption(
                label=subject["subject"], value=subject["subject_uuid"]
            )
            for subject in options
        ]

        super().__init__(
            placeholder="Select a subject...",
            options=select_options,
            min_values=1,
            max_values=1,
        )

    # fetch resources for registered subject
    async def callback(self, interaction: discord.Interaction):
        # await interaction.response.defer(ephemeral=True)
        selected_option = self.values[0]
        selected_subject_name = self.subject_names[selected_option]

        base_url = f"https://api.kaizenklass.me/api/v2/get/subjects/{selected_option}/resources"

        all_resources = []
        current_page = 1

        try:
            while True:
                url = f"{base_url}?page={current_page}"
                response = requests.get(url)
                response.raise_for_status()
                data = response.json().get("subject_resources", {})
                resources = data.get("data", [])
                all_resources.extend(resources)

                if current_page >= data.get("last_page", 1):
                    break

                current_page += 1

            embed = discord.Embed(
                title=f"Resources for {selected_subject_name}",
                color=discord.Color.red(),
            )

            for resource in all_resources:
                embed.add_field(
                    name=resource.get("title", "No Title"),
                    value=f"[Link]({resource.get('content', '#')}) - Posted by {resource.get('name', 'Unknown')}",
                    inline=False,
                )

            if not all_resources:
                embed.description = "No resources available for this subject yet."

            await interaction.followup.send(embed=embed, ephemeral=True)

            # user preferences addition in db
            async with aiosqlite.connect(DATABASE_NAME) as db:
                cursor = await db.execute(
                    "SELECT subject_uuids FROM user_preferences WHERE user_id = ?",
                    (self.user_id,),
                )
                row = await cursor.fetchone()
                if row:
                    subject_uuids = set(row[0].split(","))
                    if selected_option not in subject_uuids:
                        subject_uuids.add(selected_option)
                        new_subject_uuids = ",".join(subject_uuids)
                        await db.execute(
                            "UPDATE user_preferences SET subject_uuids = ? WHERE user_id = ?",
                            (new_subject_uuids, self.user_id),
                        )
                        await db.commit()
                        await interaction.followup.send(
                            "New subject preference has been saved!", ephemeral=True
                        )
                else:
                    await db.execute(
                        "INSERT INTO user_preferences (user_id, subject_uuids) VALUES (?, ?)",
                        (self.user_id, selected_option),
                    )
                    await db.commit()
                    await interaction.followup.send(
                        "Your first subject preference has been saved!", ephemeral=True
                    )

        except requests.RequestException as e:
            await interaction.followup.send(
                f"Error fetching resources: {e}", ephemeral=True
            )

# paginated dropdown gui
class PaginatedView(View):
    def __init__(self, subjects, user_id):
        super().__init__(timeout=180)
        self.subjects = subjects
        self.page = 0
        self.user_id = user_id
        self.message = None
        self.update_view()
        
    def set_message(self, message):
        self.message = message   

    def update_view(self):
        self.clear_items()
        self.add_item(PaginatedSelect(self.subjects, self.page, self.user_id))

        if self.page > 0:
            self.add_item(
                Button(
                    label="Previous",
                    style=discord.ButtonStyle.primary,
                    custom_id="previous",
                )
            )

        if (self.page + 1) * 25 < len(self.subjects):
            self.add_item(
                Button(
                    label="Next", style=discord.ButtonStyle.primary, custom_id="next"
                )
            )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        await self.message.edit(view=self)

# subject registration impl
class RegistrationSelect(Select):
    def __init__(self, subjects, page, user_id):
        self.subjects = subjects
        self.page = page
        self.user_id = user_id
        options_per_page = 25
        start = page * options_per_page
        end = start + options_per_page
        options = subjects[start:end]

        select_options = [
            discord.SelectOption(
                label=subject["subject"], value=subject["subject_uuid"]
            )
            for subject in options
        ]

        super().__init__(
            placeholder="Select subjects to register...",
            options=select_options,
            min_values=1,
            max_values=len(select_options),
        )

    async def callback(self, interaction: discord.Interaction):
        # await interaction.response.defer(ephemeral=True)
        selected_options = self.values

        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT subject_uuids FROM user_preferences WHERE user_id = ?",
                (self.user_id,),
            )
            row = await cursor.fetchone()
            if row:
                existing_uuids = set(row[0].split(","))
                new_uuids = set(selected_options)
                updated_uuids = existing_uuids.union(new_uuids)
                new_subject_uuids = ",".join(updated_uuids)
                await db.execute(
                    "UPDATE user_preferences SET subject_uuids = ? WHERE user_id = ?",
                    (new_subject_uuids, self.user_id),
                )
            else:
                new_subject_uuids = ",".join(selected_options)
                await db.execute(
                    "INSERT INTO user_preferences (user_id, subject_uuids) VALUES (?, ?)",
                    (self.user_id, new_subject_uuids),
                )
            await db.commit()

        await interaction.followup.send(
            "Subjects have been registered successfully!", ephemeral=True
        )
        view = self.view
        view.update_view()
        await interaction.edit_original_response(view=view)

# subject registration gui
class RegistrationView(View):
    def __init__(self, subjects, user_id):
        super().__init__(timeout=180)
        self.subjects = subjects
        self.page = 0
        self.user_id = user_id
        self.message = None
        self.update_view()

    def set_message(self, message):
        self.message = message
        
    def update_view(self):
        self.clear_items()
        self.add_item(RegistrationSelect(self.subjects, self.page, self.user_id))

        if self.page > 0:
            self.add_item(
                Button(
                    label="Previous", style=ButtonStyle.primary, custom_id="previous"
                )
            )

        if (self.page + 1) * 25 < len(self.subjects):
            self.add_item(
                Button(label="Next", style=ButtonStyle.primary, custom_id="next")
            )

        self.add_item(Button(label="Done", style=ButtonStyle.success, custom_id="done"))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        await self.message.edit(view=self)

    async def reset_selection(self):
        self.page = 0
        self.update_view()
        await self.message.edit(view=self)


class MainMenuView(View):
    def __init__(self, user_id):
        super().__init__(timeout=180)
        self.user_id = user_id

    @discord.ui.button(label="Register Subjects", style=ButtonStyle.primary)
    async def register_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        subjects = fetch_subjects()
        view = RegistrationView(subjects, self.user_id)
        message = await interaction.followup.send(
            "Select subjects to register:", view=view, ephemeral=True
        )
        view.set_message(message)
        view_dict[message.id] = view

    @discord.ui.button(label="View Subjects", style=ButtonStyle.success)
    async def subjects_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        await self.show_subjects(interaction)

    @discord.ui.button(label="Reset Preferences", style=ButtonStyle.danger)
    async def reset_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        await interaction.response.defer()
        await self.reset_preferences(interaction)

    async def show_subjects(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DATABASE_NAME) as db:
            cursor = await db.execute(
                "SELECT subject_uuids FROM user_preferences WHERE user_id = ?",
                (self.user_id,),
            )
            row = await cursor.fetchone()

        if row:
            subject_uuids = row[0].split(",")
            all_subjects = fetch_subjects()
            subjects = [
                subject
                for subject in all_subjects
                if subject["subject_uuid"] in subject_uuids
            ]
            if subjects:
                view = PaginatedView(subjects, self.user_id)
                message = await interaction.followup.send(
                    "Select a subject to view resources:", view=view, ephemeral=True
                )
                view.set_message(message)
                view_dict[message.id] = view
            else:
                await interaction.followup.send(
                    "You haven't registered any subjects yet. Use the Register Subjects button to register subjects.",
                    ephemeral=True,
                )
        else:
            await interaction.followup.send(
                "You haven't registered any subjects yet. Use the Register Subjects button to register subjects.",
                ephemeral=True,
            )

    async def reset_preferences(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DATABASE_NAME) as db:
            await db.execute(
                "DELETE FROM user_preferences WHERE user_id = ?", (self.user_id,)
            )
            await db.commit()
        await interaction.followup.send(
            "Your subject preferences have been reset. Use the Register Subjects button to register new subjects.",
            ephemeral=True,
        )


@client.event
async def on_ready():
    print(f"{client.user.name} is !#$@#$% up!!!")
    await init_db()
    await tree.sync() 


async def init_db():
    async with aiosqlite.connect(DATABASE_NAME) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                subject_uuids TEXT
            )
        """
        )
        await db.commit()

# slash commands impl
@tree.command(name="start", description="Start using the bot")
async def start(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    view = MainMenuView(user_id)
    await interaction.response.send_message(
        "Welcome! What would you like to do?", view=view, ephemeral=True
    )


@tree.command(name="register", description="Register subjects")
async def register(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    subjects = fetch_subjects()
    view = RegistrationView(subjects, user_id)
    message = await interaction.followup.send(
        "Select subjects to register:", view=view, ephemeral=True
    )
    view_dict[message.id] = view


@tree.command(name="subjects", description="View resources for registered subjects")
async def subjects(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)
    user_id = str(interaction.user.id)
    async with aiosqlite.connect(DATABASE_NAME) as db:
        cursor = await db.execute(
            "SELECT subject_uuids FROM user_preferences WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()

    if row:
        subject_uuids = row[0].split(",")
        all_subjects = fetch_subjects()
        subjects = [
            subject
            for subject in all_subjects
            if subject["subject_uuid"] in subject_uuids
        ]
        if subjects:
            view = PaginatedView(subjects, user_id)
            message = await interaction.followup.send(
                "Select a subject to view resources:", view=view, ephemeral=True
            )
            view_dict[message.id] = view
        else:
            await interaction.followup.send(
                "You haven't registered any subjects yet. Use the /register command to register subjects.",
                ephemeral=True,
            )
    else:
        await interaction.followup.send(
            "You haven't registered any subjects yet. Use the /register command to register subjects.",
            ephemeral=True,
        )


# Interaction has already been acknowledged fix -> remove defer from paginatedselect & registration select
# some conflict with discord api due to multiple defer calls, address at once in on_interaction fxn     
@client.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get("custom_id")
        if custom_id:
            view = view_dict.get(interaction.message.id)
            if isinstance(view, (PaginatedView, RegistrationView)):
                await interaction.response.defer(ephemeral=True)
                
                if custom_id == "previous":
                    if view.page > 0:
                        view.page -= 1
                elif custom_id == "next":
                    if (view.page + 1) * 25 < len(view.subjects):
                        view.page += 1
                elif custom_id == "done":
                    view.page = 0
                    await view.reset_selection()
                    await interaction.followup.send(
                        "Registration complete! You can select more subjects or close this message.",
                        ephemeral=True
                    )
                    return
                
                view.update_view()
                await interaction.edit_original_response(view=view)

client.run(DISCORD_TOKEN)
